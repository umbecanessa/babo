/** How a finished tool call should appear in the workbench / chat UI. */
export type ToolDisplayOutcome = 'ok' | 'warn' | 'error';

const HARD_FAIL_RE =
  /^(Error:|STALE FILE:|MUST READ FIRST:|BLOCKED:)/i;

const SUCCESS_MARKERS: Record<string, RegExp> = {
  write: /Successfully wrote\b/i,
  write_file: /Successfully wrote\b/i,
  create_file: /Successfully wrote\b/i,
  edit: /Successfully edited\b/i,
};

/**
 * Map backend `is_error` + result text to UI outcome.
 * Backend may set is_error for policy warnings after a successful write/edit;
 * those should not show as Failed in the workbench.
 */
export function resolveToolDisplayOutcome(
  isError: boolean,
  preview: string,
  toolName?: string,
): ToolDisplayOutcome {
  if (!isError) return 'ok';
  const text = (preview || '').trim();
  if (!text) return 'error';
  if (HARD_FAIL_RE.test(text)) return 'error';
  const name = (toolName || '').toLowerCase();
  const marker = SUCCESS_MARKERS[name];
  if (marker?.test(text)) return 'warn';
  return 'error';
}

export function toolDoneLabel(
  toolName: string,
  outcome: ToolDisplayOutcome,
): string {
  const failed = outcome === 'error';
  const warn = outcome === 'warn';
  switch (toolName) {
    case 'write':
    case 'write_file':
    case 'create_file':
      return failed ? 'Failed to write file' : warn ? 'File written (warning)' : 'File written';
    case 'read':
    case 'read_file':
      return failed ? 'Failed to read file' : 'File read';
    case 'edit':
      return failed ? 'Edit failed' : warn ? 'File edited (warning)' : 'File edited';
    case 'bash':
      return failed ? 'Command failed' : 'Command completed';
    default:
      return failed ? `${toolName} failed` : warn ? `${toolName} done (warning)` : `${toolName} done`;
  }
}
