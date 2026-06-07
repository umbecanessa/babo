import { formatAgentMode, toolWorkbenchTitle } from './workbench-labels.util';

export interface ActivityChip {
  label: string;
  value?: string;
  tone?: 'default' | 'accent' | 'muted' | 'warn' | 'success';
  /** inline = compact tag row; block = full-width message body */
  variant?: 'inline' | 'block';
}

export interface ToolWorkbenchPresentation {
  title: string;
  chips: ActivityChip[];
  subtitle?: string;
}

/** Resolved team(hint|intervene) target — delegate #, not wave member index. */
export interface TeamMemberTarget {
  delegateNumber: number;
  memberIdx: number;
  stepId?: string;
  stepLabel?: string;
  taskShort?: string;
}

export function formatTeamMemberTargetLabel(
  memberIdx: unknown,
  resolved?: TeamMemberTarget | null,
): string | undefined {
  if (memberIdx == null || memberIdx === '') return undefined;
  if (resolved != null && resolved.delegateNumber >= 0) {
    const headline = resolved.stepLabel?.trim() || resolved.taskShort?.trim();
    return headline
      ? `Sub #${resolved.delegateNumber} · ${headline}`
      : `Sub #${resolved.delegateNumber}`;
  }
  const idx = Number(memberIdx);
  if (Number.isFinite(idx) && idx >= 0) {
    return `Wave member ${idx + 1}`;
  }
  return undefined;
}

function shortTeamId(teamId: string): string {
  const t = teamId.trim();
  if (!t) return '';
  return t.length > 12 ? t.slice(0, 12) : t;
}

function formatTeamAction(action: string): string {
  const a = (action || 'inspect').toLowerCase();
  const labels: Record<string, string> = {
    hint: 'Hint',
    inspect: 'Inspect',
    launch: 'Launch',
    advance: 'Advance',
    create: 'Create',
    intervene: 'Intervene',
    disband: 'Disband',
  };
  return labels[a] || a.charAt(0).toUpperCase() + a.slice(1);
}

function formatPlanAction(action: string): string {
  const a = (action || 'read').toLowerCase();
  const labels: Record<string, string> = {
    create: 'Create',
    read: 'Read',
    update: 'Update',
    set_requirements: 'Requirements',
    set_tech_stack: 'Tech stack',
    delete: 'Archive',
    complete: 'Complete',
    continue_work: 'Continue',
    fix_dependencies: 'Fix deps',
    accept_partial: 'Accept partial',
  };
  return labels[a] || a.charAt(0).toUpperCase() + a.slice(1);
}

function truncateText(text: string, max: number): string {
  const s = (text || '').trim();
  if (s.length <= max) return s;
  return `${s.slice(0, max - 1)}…`;
}

export interface FormattedActivityText {
  headline: string;
  chips: ActivityChip[];
  body?: string;
  filePath?: string;
}

const TODO_BOILERPLATE =
  /\s*(?:ID \(use this exact value[^)]*\)[^\n]*|List:\s*\w+\s*\|\s*Priority:[^\n]*|Idle eligible:\s*\w+)/gi;

/** Strip orchestration boilerplate from tool result previews. */
export function cleanToolResultPreview(toolName: string, raw: string): string {
  let s = (raw || '').trim();
  if (!s) return '';

  if (toolName === 'todo') {
    s = s.replace(/^Added todo\s*\[[^\]]+\]:\s*/i, '');
    s = s.replace(/^Todo\s+(?:added|updated|completed)[:\s]*/i, '');
    s = s.replace(TODO_BOILERPLATE, '').trim();
    const idMatch = s.match(/\b([a-f0-9]{8})\b/i);
    if (idMatch) {
      s = s.replace(new RegExp(`\\b${idMatch[1]}\\b`, 'g'), '').replace(/\s{2,}/g, ' ').trim();
    }
  }

  if (toolName === 'plan' || toolName === 'team') {
    s = s.replace(/\s*\|\s*/g, ' · ');
  }

  return s.length > 280 ? `${s.slice(0, 277)}…` : s;
}

export function basenamePath(path: string): string {
  const p = (path || '').trim();
  if (!p) return '';
  const parts = p.split(/[/\\]/);
  return parts[parts.length - 1] || p;
}

/** Short label for UI chips (workspace-relative when possible). */
export function fileDisplayName(path: string): string {
  const norm = normalizeWorkbenchFilePath(path);
  return basenamePath(norm) || basenamePath(path) || path;
}

/** Chip label: show project folder + path when we only have a bare filename. */
export function filePathChipLabel(path: string, projectDir?: string): string {
  const { parent, name } = fileChipParts(path, projectDir);
  if (parent) return `${parent}/${name}`;
  return name || path;
}

/** Compact chip: immediate parent folder + file name (full path in title attr). */
export function fileChipParts(
  path: string,
  projectDir?: string,
): { parent: string; name: string } {
  const norm = normalizeWorkbenchFilePath(path);
  if (!norm) {
    return { parent: '', name: path };
  }
  const enriched = projectDir
    ? enrichWorkspaceRelativePathForDisplay(norm, projectDir)
    : norm;
  const parts = enriched.replace(/\\/g, '/').split('/').filter(Boolean);
  if (!parts.length) {
    return { parent: '', name: path };
  }
  if (parts.length === 1) {
    return { parent: '', name: parts[0] };
  }
  return {
    parent: parts[parts.length - 2],
    name: parts[parts.length - 1],
  };
}

function enrichWorkspaceRelativePathForDisplay(
  rel: string,
  projectDir: string,
): string {
  const p = rel.replace(/\\/g, '/').replace(/^\/+/, '');
  const pd = projectDir.replace(/\\/g, '/').replace(/^\/+|\/+$/g, '');
  if (!pd) return p;
  if (p === pd || p.startsWith(`${pd}/`)) return p;
  if (!p.includes('/')) return `${pd}/${p}`;
  return p;
}

/** Parse tool args from WS payloads (object, JSON string, or vLLM input envelope). */
export function normalizeToolArguments(raw: unknown): Record<string, unknown> {
  let args: Record<string, unknown> = {};
  if (raw == null) {
    return args;
  }
  if (typeof raw === 'string') {
    try {
      const parsed = JSON.parse(raw) as unknown;
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
        args = { ...(parsed as Record<string, unknown>) };
      }
    } catch {
      return {};
    }
  } else if (typeof raw === 'object' && !Array.isArray(raw)) {
    args = { ...(raw as Record<string, unknown>) };
  } else {
    return {};
  }

  if (args['input'] != null && Object.keys(args).length === 1) {
    let inner: unknown = args['input'];
    if (typeof inner === 'string') {
      try {
        inner = JSON.parse(inner);
      } catch {
        /* keep string */
      }
    }
    if (inner && typeof inner === 'object' && !Array.isArray(inner)) {
      args = { ...(inner as Record<string, unknown>) };
    }
  }

  for (const key of ['path', 'file_path', 'source', 'destination', 'target']) {
    const v = args[key];
    if (typeof v !== 'string' || !v.trim().startsWith('{')) {
      continue;
    }
    try {
      const inner = JSON.parse(v) as { path?: string };
      if (typeof inner?.path === 'string' && inner.path) {
        args[key] = inner.path;
      }
    } catch {
      /* ignore */
    }
  }

  return args;
}

/** Pull a path from chat tool_progress content like "Reading C:\\foo\\bar.md…". */
export function extractPathFromToolContent(content: string): string | undefined {
  const m = (content || '').match(
    /^(?:Reading|Writing|Editing)\s+(.+?)(?:\.\.\.|$)/i,
  );
  if (!m?.[1]) {
    return undefined;
  }
  const norm = normalizeWorkbenchFilePath(m[1].trim());
  return norm || undefined;
}

/** Tools that target a workspace file path (shown as clickable chips in workbench). */
export const FILE_PATH_TOOLS = new Set([
  'read',
  'read_file',
  'write',
  'write_file',
  'create_file',
  'edit',
  'delete_file',
  'move_file',
  'glob',
  'grep',
]);

export function isFilePathTool(toolName: string): boolean {
  return FILE_PATH_TOOLS.has(toolName);
}

/**
 * Normalize absolute agent/workspace paths to a Projects → Files friendly path.
 */
/** Paths that must not become file chips or editor open targets. */
export function isInvalidWorkspacePathToken(raw: string): boolean {
  const p = (raw || '').trim().replace(/\\/g, '/').replace(/\/+$/, '');
  return !p || p === '/' || p === '.' || p === '..';
}

export function normalizeWorkbenchFilePath(raw: string): string {
  let p = (raw || '').trim();
  if (!p) return '';
  p = p.replace(/\\/g, '/');
  if (isInvalidWorkspacePathToken(p)) return '';
  // Read-tool previews sometimes embed "path|line|content"
  if (p.includes('|')) {
    p = p.split('|')[0].trim();
  }

  const lower = p.toLowerCase();
  const wsIdx = lower.indexOf('/workspace/');
  if (wsIdx >= 0) {
    return p.slice(wsIdx + '/workspace/'.length).replace(/^\/+/, '');
  }

  const agentsMatch = p.match(
    /\/data\/agents\/[0-9a-f-]{36}\/workspace\/(.+)$/i,
  );
  if (agentsMatch?.[1]) {
    return agentsMatch[1].replace(/^\/+/, '');
  }

  const roamingMatch = p.match(
    /babo-desktop\/data\/agents\/[0-9a-f-]{36}\/workspace\/(.+)$/i,
  );
  if (roamingMatch?.[1]) {
    return roamingMatch[1].replace(/^\/+/, '');
  }

  return p;
}

/** Extract a filesystem path from read/write tool args or result text. */
export function extractFilePath(
  toolName: string,
  args?: Record<string, unknown>,
  preview?: string,
): string | undefined {
  const paths = collectFilePaths(toolName, args, preview);
  return paths[0];
}

/** All file paths referenced by a tool call (usually one). */
export function collectFilePaths(
  toolName: string,
  args?: Record<string, unknown> | unknown,
  preview?: string,
  toolContent?: string,
): string[] {
  const out: string[] = [];
  const normalizedArgs = normalizeToolArguments(args);
  const add = (raw: unknown) => {
    if (typeof raw !== 'string' || !raw.trim()) return;
    const norm = normalizeWorkbenchFilePath(raw.trim());
    if (!norm || isInvalidWorkspacePathToken(norm) || out.includes(norm)) {
      return;
    }
    if (norm.endsWith('/')) return;
    out.push(norm);
  };

  if (isFilePathTool(toolName)) {
    add(normalizedArgs['path']);
    add(normalizedArgs['file_path']);
    add(normalizedArgs['destination']);
    add(normalizedArgs['source']);
    add(normalizedArgs['target']);
  }

  if (
    toolName === 'read'
    || toolName === 'read_file'
    || toolName === 'write'
    || toolName === 'write_file'
    || toolName === 'create_file'
    || toolName === 'edit'
  ) {
    const previewText = preview || '';
    const abs = previewText.match(
      /(?:^|\s)([A-Za-z]:\\[^\s—\n|]+|\/[^\s—\n|]+)/,
    );
    if (abs?.[1]) {
      add(abs[1]);
    }
    const wroteTo = previewText.match(
      /\b(?:to|into|wrote(?:\s+\d+\s+lines?)?(?:\s*\([^)]*\))?\s+to)\s+([a-zA-Z0-9_][a-zA-Z0-9_./\\-]*\.[a-zA-Z0-9]+)/i,
    );
    if (wroteTo?.[1]) {
      add(wroteTo[1]);
    }
    const relPath = previewText.match(
      /\b(?:read|reading|write|writing|edit|editing|file:?)\s+([a-zA-Z0-9_][a-zA-Z0-9_./\\-]*\.[a-zA-Z0-9]+)/i,
    );
    if (relPath?.[1]) {
      add(relPath[1]);
    }
    const fromContent = extractPathFromToolContent(toolContent || '');
    if (fromContent) {
      add(fromContent);
    }
  }

  return out;
}

/** Parse agent routing / team check-in injection text into UI-friendly parts. */
export function parseAgentMessageText(raw: string): FormattedActivityText {
  const text = (raw || '').trim();
  if (!text) {
    return { headline: 'Update', chips: [] };
  }

  const chips: ActivityChip[] = [];
  let rest = text;

  const agentMsg = rest.match(/\[AGENT_MSG\|([^\]]+)\]/i);
  if (agentMsg) {
    const inner = agentMsg[1];
    const agentId = inner.match(/agent_id=([^|]+)/i)?.[1];
    const batch = inner.match(/batch=([^|]+)/i)?.[1];
    if (agentId) {
      chips.push({ label: 'Agent', value: agentId.slice(0, 8), tone: 'muted' });
    }
    if (batch) {
      chips.push({ label: 'Batch', value: batch.slice(0, 8), tone: 'muted' });
    }
    rest = rest.replace(agentMsg[0], '').trim();
  }

  const teamCheck = rest.match(/\[TEAM CHECK-BACK[^\]]*—\s*([^\]]+)\]/i);
  if (teamCheck) {
    chips.push({ label: 'Check-in', value: teamCheck[1].trim(), tone: 'accent' });
    rest = rest.replace(teamCheck[0], '').trim();
  }

  const teamLine = rest.match(/Team\s+(.+?)\s*\[(team_[^\]]+)\]/i);
  let headline = 'Orchestrator check-in';
  if (teamLine) {
    headline = teamLine[1].trim();
    chips.push({ label: 'Team', value: teamLine[2], tone: 'default' });
    rest = rest.replace(teamLine[0], '').trim();
  } else {
    const firstLine = rest.split('\n').find((l) => l.trim())?.trim() || '';
    if (firstLine && firstLine.length < 120 && !firstLine.startsWith('[')) {
      headline = firstLine;
      rest = rest.replace(firstLine, '').trim();
    }
  }

  const body = rest
    .replace(/^\d+\)\s*/gm, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim();

  return {
    headline,
    chips,
    body: body.length > 0 ? body : undefined,
  };
}

export function formatIterationToolSummary(
  toolCalls: Array<{ name?: string; call_id?: string; arguments?: Record<string, unknown> }>,
  toolResults: Array<{ success?: boolean }>,
  metaByCallId: Map<string, { title: string; subtitle?: string }>,
  lastMode: string,
): string {
  if (!toolCalls?.length) return '';
  const parts: string[] = [];
  for (let i = 0; i < toolCalls.length; i++) {
    const tc = toolCalls[i];
    const name = tc.name || 'tool';
    const callId = tc.call_id || '';
    const cached = callId ? metaByCallId.get(callId) : undefined;
    if (cached?.title) {
      parts.push(cached.title);
      continue;
    }
    const args = normalizeToolArguments(tc.arguments ?? {});
    const parsed = toolWorkbenchTitle(name, args, { lastMode });
    parts.push(parsed.title);
  }
  const ok = toolResults.filter((r) => r.success !== false).length;
  const total = toolResults.length;
  const summary = parts.join(' · ');
  return total > 0 ? `${summary} (${ok}/${total} ok)` : summary;
}

/** Strip leading path echoes from read/write result previews. */
export function stripPathFromPreview(preview: string, filePath?: string): string {
  let s = (preview || '').trim();
  if (!s) return '';
  if (filePath) {
    const base = basenamePath(filePath);
    const abs = filePath.replace(/\\/g, '/');
    for (const prefix of [abs, filePath, base]) {
      if (prefix && s.startsWith(prefix)) {
        s = s.slice(prefix.length).replace(/^[\s|—:-]+/, '').trim();
      }
    }
  }
  const linePath = s.match(/^(?:\d+\|)?([A-Za-z]:\\[^\n]+|\/[^\n]+)/);
  if (linePath?.[1] && s.length < 120) {
    return '';
  }
  return s;
}

export function previewToChips(toolName: string, preview: string): ActivityChip[] {
  const cleaned = cleanToolResultPreview(toolName, preview);
  if (!cleaned) return [];

  if (isFilePathTool(toolName)) {
    const body = stripPathFromPreview(cleaned);
    if (!body || body.length < 8) {
      return [];
    }
    return [{ label: 'Preview', value: body, tone: 'muted', variant: 'block' }];
  }

  if (toolName === 'todo') {
    const chips: ActivityChip[] = [];
    const pri = cleaned.match(/priority[:\s]+(\w+)/i)?.[1];
    const list = cleaned.match(/list[:\s]+(\w+)/i)?.[1];
    if (list) chips.push({ label: 'List', value: list, tone: 'muted' });
    if (pri) chips.push({ label: 'Priority', value: pri, tone: pri === 'high' ? 'warn' : 'default' });
    const titleOnly = cleaned.replace(/List:\s*\w+/i, '').replace(/Priority:\s*\w+/i, '').trim();
    if (titleOnly) chips.unshift({ label: 'Task', value: titleOnly, tone: 'default' });
    return chips.length ? chips : [{ label: 'Note', value: cleaned, tone: 'muted' }];
  }

  return [{
    label: 'Result',
    value: cleaned,
    tone: 'muted',
    variant: cleaned.length > 96 ? 'block' : 'inline',
  }];
}

/** Structured workbench row for team tool calls (chips, no duplicate prose). */
export function teamWorkbenchPresentation(
  args: Record<string, unknown>,
  preview: string,
  opts?: { delegateNumber?: number; memberTarget?: TeamMemberTarget | null },
): ToolWorkbenchPresentation {
  const action = String(args['action'] || 'inspect').toLowerCase();
  const teamId = String(args['team_id'] || '').trim();
  const message = String(args['message'] || '').trim();
  const decision = String(args['decision'] || '').trim();
  const memberLabel = formatTeamMemberTargetLabel(
    args['member'],
    opts?.memberTarget,
  );

  const chips: ActivityChip[] = [];
  if (teamId) {
    chips.push({
      label: 'Team',
      value: shortTeamId(teamId),
      tone: 'default',
      variant: 'inline',
    });
  }
  chips.push({
    label: 'Action',
    value: formatTeamAction(action),
    tone: 'accent',
    variant: 'inline',
  });

  const sender =
    opts?.delegateNumber != null
      ? `Sub #${opts.delegateNumber}`
      : 'Orchestrator';

  if (action === 'hint' || action === 'intervene') {
    chips.push({ label: 'From', value: sender, tone: 'muted', variant: 'inline' });
    if (memberLabel) {
      chips.push({ label: 'To', value: memberLabel, tone: 'accent', variant: 'inline' });
    }
  }

  if (action === 'hint' && message) {
    chips.push({
      label: 'Message',
      value: truncateText(message, 2400),
      tone: 'default',
      variant: 'block',
    });
    return { title: 'Team hint', chips };
  }

  if (action === 'intervene') {
    if (decision) {
      chips.push({
        label: 'Decision',
        value: decision,
        tone: 'warn',
        variant: 'inline',
      });
    }
    if (message) {
      chips.push({
        label: 'Message',
        value: truncateText(message, 1200),
        tone: 'default',
        variant: 'block',
      });
    }
    return { title: 'Team intervention', chips };
  }

  const titles: Record<string, string> = {
    inspect: 'Team inspect',
    launch: 'Team launch',
    advance: 'Advance wave',
    create: 'Create team',
    disband: 'Disband team',
  };

  const cleaned = cleanToolResultPreview('team', preview);
  if (cleaned) {
    const wave = cleaned.match(/wave\s*(\d+)/i)?.[1];
    const members = cleaned.match(/(\d+)\s*member/i)?.[1];
    if (wave) {
      chips.push({ label: 'Wave', value: wave, tone: 'muted', variant: 'inline' });
    }
    if (members) {
      chips.push({
        label: 'Members',
        value: members,
        tone: 'muted',
        variant: 'inline',
      });
    }
    if (!wave && !members) {
      if (cleaned.length <= 96) {
        chips.push({
          label: 'Status',
          value: cleaned,
          tone: 'muted',
          variant: 'inline',
        });
      } else {
        chips.push({
          label: 'Summary',
          value: truncateText(cleaned, 400),
          tone: 'muted',
          variant: 'block',
        });
      }
    }
  }

  return {
    title: titles[action] || 'Team',
    chips,
    subtitle: undefined,
  };
}

/** @deprecated Use teamWorkbenchPresentation */
export function teamToolChips(
  args: Record<string, unknown>,
  preview: string,
): ActivityChip[] {
  return teamWorkbenchPresentation(args, preview).chips;
}

/** Structured workbench row for plan tool calls. */
export function planWorkbenchPresentation(
  args: Record<string, unknown>,
  preview: string,
): ToolWorkbenchPresentation {
  const action = String(args['action'] || 'read').toLowerCase();
  const planId = String(args['plan_id'] || '').trim();
  const stepId = String(args['step_id'] || '').trim();
  const status = String(args['status'] || '').trim();
  const title = String(args['title'] || '').trim();

  const chips: ActivityChip[] = [];
  if (planId) {
    chips.push({
      label: 'Plan',
      value: planId.length > 14 ? planId.slice(0, 14) : planId,
      tone: 'default',
      variant: 'inline',
    });
  }
  chips.push({
    label: 'Action',
    value: formatPlanAction(action),
    tone: 'accent',
    variant: 'inline',
  });
  if (stepId) {
    chips.push({ label: 'Step', value: stepId, tone: 'muted', variant: 'inline' });
  }
  if (status) {
    chips.push({ label: 'Status', value: status, tone: 'warn', variant: 'inline' });
  }
  if (title) {
    chips.push({
      label: 'Title',
      value: truncateText(title, 80),
      tone: 'muted',
      variant: 'inline',
    });
  }

  const actionTitles: Record<string, string> = {
    create: 'Create plan',
    read: 'Read plan',
    update: 'Update plan',
    delete: 'Archive plan',
    complete: 'Complete plan',
    continue_work: 'Continue plan',
    fix_dependencies: 'Fix dependencies',
    accept_partial: 'Accept partial',
  };

  const cleaned = cleanToolResultPreview('plan', preview);
  if (cleaned && !title) {
    if (cleaned.length <= 96) {
      chips.push({
        label: 'Summary',
        value: cleaned,
        tone: 'muted',
        variant: 'inline',
      });
    } else {
      chips.push({
        label: 'Summary',
        value: truncateText(cleaned, 400),
        tone: 'muted',
        variant: 'block',
      });
    }
  }

  return {
    title: actionTitles[action] || 'Plan',
    chips,
  };
}
