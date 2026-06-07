import { ChatMessage, MessageAttachment } from './websocket.service';
import { parseThinking } from '../../shared/signal-utils';
import { agenticAbortLabel } from '../../features/chat/orchestration-ui.util';
import { toolWorkbenchTitle } from './workbench-labels.util';
import {
  collectFilePaths,
  formatIterationToolSummary,
  normalizeToolArguments,
  planWorkbenchPresentation,
  teamWorkbenchPresentation,
  type ActivityChip,
} from './activity-format.util';

const SYSTEM_INJECTION_PATTERNS = [
  '[REMEMBERED',
  'Review: did you complete ALL parts',
  '[SKILL ONBOARDING',
  'Good plan. Now EXECUTE',
  'You wrote files to the skills directory but did NOT call',
  'SELF-CORRECTION CHECKPOINT',
  'ANS CHECKPOINT',
  '--- ANS CHECKPOINT',
  'Do NOT describe what you would do',
  'MANDATORY — without it the skill will never load',
  'ERRORS OBSERVED:',
  'STRESS LEVEL: cortisol=',
];

const ATTACHMENT_BLOCK_RE =
  /^\[The user attached \d+ file\(s\):\n([\s\S]*?)\]\n\n?([\s\S]*)$/;
const ATTACHMENT_LINE_RE =
  /^\s*-\s+(.+?)\s+\(([^,]+),\s*([^)]+)\)\s*\n\s*read\(path="([^"]+)"\)/gm;

export function isChatSystemInjection(text: string): boolean {
  const t = text.trim();
  return SYSTEM_INJECTION_PATTERNS.some(p => t.includes(p));
}

export interface TranscriptRestoreOptions {
  sessionKey?: string;
  onPlanHydrate?: (meta: Record<string, unknown>) => void;
}

interface TranscriptRow {
  role?: string;
  content?: string;
  reasoning?: string;
  timestamp?: number;
  ts?: string;
  attachments?: MessageAttachment[];
  metadata?: Record<string, unknown>;
}

function timestampFromRow(m: TranscriptRow): Date {
  const ts = m.timestamp;
  if (typeof ts === 'number' && ts > 0) {
    return new Date(ts > 1e12 ? ts : ts * 1000);
  }
  if (typeof m.ts === 'string') {
    const parsed = Date.parse(m.ts);
    if (!Number.isNaN(parsed)) return new Date(parsed);
  }
  return new Date();
}

function kindToMime(kind: string): string {
  const k = kind.trim().toLowerCase();
  if (k === 'text') return 'text/plain';
  if (k === 'document') return 'application/pdf';
  if (k === 'spreadsheet') return 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
  if (k === 'presentation') return 'application/vnd.openxmlformats-officedocument.presentationml.presentation';
  if (k === 'archive') return 'application/zip';
  if (k === 'audio') return 'audio/mpeg';
  if (k === 'image') return 'image/png';
  if (k === 'folder') return 'inode/directory';
  return 'application/octet-stream';
}

function parseSizeBytes(sizeLabel: string): number | undefined {
  const m = sizeLabel.trim().match(/^([\d.]+)\s*(B|KB|MB|GB)?$/i);
  if (!m) return undefined;
  const n = parseFloat(m[1]);
  if (!Number.isFinite(n)) return undefined;
  const unit = (m[2] || 'B').toUpperCase();
  const mult = unit === 'GB' ? 1e9 : unit === 'MB' ? 1e6 : unit === 'KB' ? 1e3 : 1;
  return Math.round(n * mult);
}

/** Split persisted attachment injection from the user's visible message text. */
export function parseTranscriptUserContent(
  row: TranscriptRow,
): { content: string; attachments?: MessageAttachment[] } {
  if (Array.isArray(row.attachments) && row.attachments.length > 0) {
    return {
      content: (row.content || '').trim(),
      attachments: row.attachments,
    };
  }

  const raw = row.content || '';
  const match = raw.match(ATTACHMENT_BLOCK_RE);
  if (!match) {
    return { content: raw.trim() };
  }

  const block = match[1];
  const userText = (match[2] || '').trim();
  const attachments: MessageAttachment[] = [];
  let lineMatch: RegExpExecArray | null;
  ATTACHMENT_LINE_RE.lastIndex = 0;
  while ((lineMatch = ATTACHMENT_LINE_RE.exec(block)) !== null) {
    const [, name, kind, sizeLabel, absPath] = lineMatch;
    const fileName = name.trim();
    attachments.push({
      name: fileName,
      path: absPath,
      mime_type: kindToMime(kind),
      size: parseSizeBytes(sizeLabel),
    });
  }

  return {
    content: userText,
    attachments: attachments.length ? attachments : undefined,
  };
}

function stepProse(stepEv: Record<string, unknown>): string {
  const prose = stepEv['prose'] ?? stepEv['response_text'];
  return typeof prose === 'string' ? prose.trim() : '';
}

function appendAgenticTrace(
  restored: ChatMessage[],
  meta: Record<string, unknown>,
  sessionKey?: string,
): void {
  const iterations = meta['iterations'] as number | undefined;
  const toolCalls = meta['tool_calls'] as number | undefined;
  restored.push({
    type: 'agentic_start' as any,
    content: `Agent ran autonomous task (${iterations || 0} steps, ${toolCalls || 0} tool calls)`,
    timestamp: new Date(),
    ...(sessionKey ? { sessionKey } : {}),
  });

  const events = meta['events'];
  if (Array.isArray(events)) {
    for (const ev of events) {
      const stepEv = ev as Record<string, unknown>;
      const prose = stepProse(stepEv);
      if (prose && !isChatSystemInjection(prose)) {
        const thought = parseThinking(prose);
        restored.push({
          type: 'assistant',
          content: thought.response || prose,
          reasoning: thought.thinking || undefined,
          timestamp: new Date(),
          ...(sessionKey ? { sessionKey } : {}),
        });
      }

      const toolCallsList = (stepEv['tool_calls'] || []) as Array<Record<string, unknown>>;
      const results = (stepEv['tool_results'] || []) as Array<Record<string, unknown>>;
      const summary = formatIterationToolSummary(
        toolCallsList.map(tc => ({
          name: String(tc['name'] || tc['tool_name'] || 'tool'),
          call_id: String(tc['call_id'] || ''),
          arguments: normalizeToolArguments(tc['arguments'] ?? {}),
        })),
        results.map(r => ({ success: r['success'] !== false })),
        new Map(),
        '',
      );
      const successes = results.filter(r => r['success'] !== false).length;
      restored.push({
        type: 'agentic_iteration' as any,
        content: summary
          ? `Step ${stepEv['step']}: ${summary}`
          : `Step ${stepEv['step']}: processing (${successes}/${results.length} succeeded)`,
        timestamp: new Date(),
        sessionKey,
        agentic: {
          step: stepEv['step'] as number,
          maxSteps: iterations || 0,
          toolCalls: stepEv['tool_calls'] || [],
          toolResults: results,
          hormones: stepEv['hormones'] || {},
          durationMs: (stepEv['duration_ms'] || stepEv['durationMs'] || 0) as number,
        },
      } as any);
    }
  }

  const restoredEvents = (Array.isArray(events) ? events : []).map((ev: unknown) => {
    const stepEv = ev as Record<string, unknown>;
    const tcList = (stepEv['tool_calls'] || []) as Array<Record<string, unknown>>;
    const trList = (stepEv['tool_results'] || []) as Array<Record<string, unknown>>;
    return {
      step: stepEv['step'],
      toolCalls: tcList.map(tc => ({ name: String(tc['name'] || 'tool') })),
      toolResults: trList.map(tr => ({ success: tr['success'] !== false })),
      durationMs: (stepEv['duration_ms'] || stepEv['durationMs'] || 0) as number,
    };
  });

  const aborted = Boolean(meta['aborted']);
  const abortReason = String(meta['abort_reason'] || '');
  const autonomous = Boolean(meta['autonomous']);

  restored.push({
    type: 'agentic_complete' as any,
    content: aborted
      ? agenticAbortLabel(true, abortReason, autonomous)
      : 'Task complete',
    timestamp: new Date(),
    sessionKey,
    agenticComplete: {
      totalSteps: iterations || 0,
      totalToolCalls: toolCalls || 0,
      aborted,
      abortReason,
      durationMs: 0,
      hormones: {},
      events: restoredEvents,
    },
  } as any);
}

/** Rebuild Chat UI messages from server transcript rows (WS history or REST). */
export function restoreChatMessagesFromTranscript(
  raw: unknown[],
  options: TranscriptRestoreOptions = {},
): ChatMessage[] {
  const restored: ChatMessage[] = [];
  const sessionKey = options.sessionKey;

  for (const row of raw) {
    if (!row || typeof row !== 'object') continue;
    const m = row as TranscriptRow;
    const isAssistant = m.role !== 'user';
    const meta = (m.metadata || {}) as Record<string, unknown>;
    const ts = timestampFromRow(m);

    if (isAssistant && meta['agentic']) {
      appendAgenticTrace(restored, meta, sessionKey);
      const planSteps = meta['plan_steps'];
      if (Array.isArray(planSteps) && planSteps.length > 0) {
        options.onPlanHydrate?.(meta);
      }
    }

    if (isAssistant && m.content) {
      if (meta['autonomous'] && meta['communicated']) continue;
      const text = String(m.content);
      if (isChatSystemInjection(text)) continue;
      const thought = parseThinking(text);
      const reasoning = (
        (typeof m.reasoning === 'string' && m.reasoning.trim())
        || thought.thinking
        || undefined
      );
      restored.push({
        type: 'assistant',
        content: thought.response || text,
        reasoning,
        timestamp: ts,
        ...(sessionKey ? { sessionKey } : {}),
      });
    } else if (m.role === 'user' && (m.content || m.attachments?.length)) {
      const parsed = parseTranscriptUserContent(m);
      if (!parsed.content.trim() && !parsed.attachments?.length) continue;
      if (parsed.content && isChatSystemInjection(parsed.content)) continue;
      restored.push({
        type: 'user',
        content: parsed.content,
        attachments: parsed.attachments,
        timestamp: ts,
        ...(sessionKey ? { sessionKey } : {}),
      });
    }
  }

  return restored;
}

export interface WorkbenchRestoreEntry {
  kind: 'agentic' | 'tool';
  title: string;
  subtitle?: string;
  status?: 'ok' | 'warn' | 'error';
  toolLabel?: string;
  chips?: ActivityChip[];
  filePaths?: string[];
  mode?: string;
}

function toolRestoreEntry(
  tc: Record<string, unknown>,
  tr: Record<string, unknown> | undefined,
  lastMode: string,
): { entry: WorkbenchRestoreEntry; lastMode: string } {
  const name = String(tc['name'] || tc['tool_name'] || 'tool');
  const args = normalizeToolArguments(tc['arguments'] ?? {});
  const ok = tr ? tr['success'] !== false : true;
  const preview = String(tr?.['result_preview'] || tr?.['preview'] || '');

  let title: string;
  let subtitle: string | undefined;
  let toolLabel: string;
  let chips: ActivityChip[] | undefined;
  let filePaths: string[] | undefined;
  let mode: string | undefined;

  if (name === 'plan') {
    const pres = planWorkbenchPresentation(args, preview);
    title = pres.title;
    subtitle = pres.subtitle;
    toolLabel = pres.title.startsWith('Plan') ? 'Plan' : 'Plan';
    chips = pres.chips;
  } else if (name === 'team') {
    const pres = teamWorkbenchPresentation(args, preview);
    title = pres.title;
    subtitle = pres.subtitle;
    toolLabel = 'Team';
    chips = pres.chips;
  } else {
    const parsed = toolWorkbenchTitle(name, args, { lastMode });
    title = parsed.title;
    subtitle = parsed.subtitle;
    toolLabel = parsed.toolLabel;
    if (parsed.modeTransition) {
      mode = parsed.modeTransition;
      lastMode = parsed.modeTransition;
    }
    filePaths = collectFilePaths(name, args);
  }

  return {
    entry: {
      kind: 'tool',
      title,
      subtitle,
      status: ok ? 'ok' : 'error',
      toolLabel,
      chips,
      filePaths: filePaths?.length ? filePaths : undefined,
      mode,
    },
    lastMode,
  };
}

/** Rebuild workbench rows from persisted agentic transcript metadata. */
export function buildWorkbenchRestoreEntries(rows: unknown[]): WorkbenchRestoreEntry[] {
  const out: WorkbenchRestoreEntry[] = [];
  let lastMode = '';

  for (const row of rows) {
    if (!row || typeof row !== 'object') continue;
    const meta = ((row as TranscriptRow).metadata || {}) as Record<string, unknown>;
    if (!meta['agentic']) continue;

    const iterations = Number(meta['iterations'] || 0);
    const toolCallsTotal = Number(meta['tool_calls'] || 0);
    const aborted = Boolean(meta['aborted']);
    const events = Array.isArray(meta['events']) ? meta['events'] : [];

    out.push({
      kind: 'agentic',
      title: aborted ? 'Task stopped (restored)' : 'Agent task (restored)',
      subtitle: iterations
        ? `Up to ${iterations} steps`
        : `${toolCallsTotal || events.length} tool call(s)`,
      status: aborted ? 'error' : 'ok',
      toolLabel: 'Task',
    });

    for (const ev of events) {
      const stepEv = ev as Record<string, unknown>;
      const step = Number(stepEv['step'] || 0);
      const tcList = (stepEv['tool_calls'] || []) as Array<Record<string, unknown>>;
      const trList = (stepEv['tool_results'] || []) as Array<Record<string, unknown>>;
      const durationMs = Number(stepEv['duration_ms'] || stepEv['durationMs'] || 0);
      const prose = stepProse(stepEv);

      if (prose) {
        out.push({
          kind: 'agentic',
          title: 'Agent message',
          subtitle: prose.length > 140 ? `${prose.slice(0, 140)}…` : prose,
          status: 'ok',
          toolLabel: 'Prose',
        });
      }

      if (tcList.length) {
        const summary = formatIterationToolSummary(
          tcList.map(tc => ({
            name: String(tc['name'] || tc['tool_name'] || 'tool'),
            call_id: String(tc['call_id'] || ''),
            arguments: normalizeToolArguments(tc['arguments'] ?? {}),
          })),
          trList.map(r => ({ success: r['success'] !== false })),
          new Map(),
          lastMode,
        );
        out.push({
          kind: 'agentic',
          title: step ? `Step ${step}` : 'Step',
          subtitle: summary || undefined,
          status: trList.some(r => r['success'] === false) ? 'warn' : 'ok',
          toolLabel: 'Step',
        });
      }

      tcList.forEach((tc, idx) => {
        const built = toolRestoreEntry(tc, trList[idx], lastMode);
        lastMode = built.lastMode;
        out.push({
          ...built.entry,
          subtitle: built.entry.subtitle
            ?? (tcList.length === 1 && durationMs
              ? `${(durationMs / 1000).toFixed(1)}s`
              : undefined),
        });
      });
    }

    out.push({
      kind: 'agentic',
      title: aborted ? 'Task stopped' : 'Task complete',
      subtitle: `${iterations || events.length} steps, ${toolCallsTotal} tools`,
      status: aborted ? 'error' : 'ok',
      toolLabel: 'Task',
    });
  }

  return out;
}

export function transcriptHasAgenticTrace(rows: unknown[]): boolean {
  return rows.some(row => {
    if (!row || typeof row !== 'object') return false;
    return Boolean(((row as TranscriptRow).metadata || {})['agentic']);
  });
}
