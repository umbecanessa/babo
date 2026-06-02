import type { WorkbenchEntry } from './chat-workbench.service';

/** Errors where a delegate can request path access from the orchestrator. */
const FILE_SCOPE_ESCALATE_RE =
  /PATH NOT IN YOUR ASSIGNMENT|FILE LOCKED:|FILE OWNED BY TEAMMATE|outside your wave-\d+ file scope/i;

const _OUTPUT_CHIP_LABELS = ['Preview', 'Output', 'Result', 'Error', 'Warning'] as const;

/** Tool result body for display / escalate hints (neutral, not the red error panel). */
export function extractEntryOutputText(e: WorkbenchEntry): string | null {
  for (const label of _OUTPUT_CHIP_LABELS) {
    const block = (e.chips ?? []).find((c) => c.label === label && c.value);
    if (block?.value) return block.value;
  }
  if (e.subtitle?.trim()) return e.subtitle.trim();
  if (e.detail?.trim()) return e.detail.trim();
  return null;
}

export function extractEntryErrorText(e: WorkbenchEntry): string | null {
  if (e.status !== 'error' && e.status !== 'warn') return null;
  return extractEntryOutputText(e);
}

/** Hide "Full output" when it repeats the inline error body. */
export function shouldShowErrorDetail(
  e: WorkbenchEntry,
  errorText: string | null,
): boolean {
  const detail = e.detail?.trim();
  if (!detail) return false;
  const err = errorText?.trim();
  if (!err) return true;
  if (detail === err) return false;
  if (detail.startsWith(err) && detail.length <= err.length + 48) return false;
  return detail.length > err.length + 20;
}

export function escalateHintForEntry(
  e: WorkbenchEntry,
  errorText: string | null,
): string | null {
  if (e.status !== 'error' || typeof e.delegateNumber !== 'number' || !errorText) {
    return null;
  }
  if (!FILE_SCOPE_ESCALATE_RE.test(errorText)) return null;
  return (
    'Need access outside your assignment? Ask the orchestrator for permission with '
    + "escalate(reason='file_access', paths=['…'], message='why you need it')."
  );
}
