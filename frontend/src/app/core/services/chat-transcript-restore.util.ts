import { ChatMessage } from './websocket.service';
import { parseThinking } from '../../shared/signal-utils';
import { agenticAbortLabel } from '../../features/chat/orchestration-ui.util';

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
      const toolCallsList = (stepEv['tool_calls'] || []) as Array<Record<string, unknown>>;
      const toolNames = toolCallsList
        .map(tc => String(tc['name'] || tc['tool_name'] || 'tool'))
        .join(', ');
      const results = (stepEv['tool_results'] || []) as Array<Record<string, unknown>>;
      const successes = results.filter(r => r['success'] !== false).length;
      restored.push({
        type: 'agentic_iteration' as any,
        content: `Step ${stepEv['step']}: ${toolNames || 'processing'} (${successes}/${results.length} succeeded)`,
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
    } else if (m.role === 'user' && m.content) {
      const text = String(m.content);
      if (!text.trim()) continue;
      if (isChatSystemInjection(text)) continue;
      restored.push({
        type: 'user',
        content: text,
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
}

/** Rebuild workbench rows from persisted agentic transcript metadata. */
export function buildWorkbenchRestoreEntries(rows: unknown[]): WorkbenchRestoreEntry[] {
  const out: WorkbenchRestoreEntry[] = [];

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
      const toolNames = tcList
        .map(tc => String(tc['name'] || tc['tool_name'] || 'tool'))
        .join(', ');
      const successes = trList.filter(r => r['success'] !== false).length;
      const durationMs = Number(stepEv['duration_ms'] || stepEv['durationMs'] || 0);

      out.push({
        kind: 'agentic',
        title: step ? `Step ${step}` : 'Step',
        subtitle: toolNames
          ? `${toolNames} (${successes}/${Math.max(trList.length, tcList.length)} ok)`
          : 'Processing',
        status: trList.some(r => r['success'] === false) ? 'warn' : 'ok',
        toolLabel: 'Step',
      });

      tcList.forEach((tc, idx) => {
        const name = String(tc['name'] || tc['tool_name'] || 'tool');
        const tr = trList[idx];
        const ok = tr ? tr['success'] !== false : true;
        out.push({
          kind: 'tool',
          title: name,
          subtitle: tcList.length === 1 && durationMs
            ? `${(durationMs / 1000).toFixed(1)}s`
            : undefined,
          status: ok ? 'ok' : 'error',
          toolLabel: name.charAt(0).toUpperCase() + name.slice(1),
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
