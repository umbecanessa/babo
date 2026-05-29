import type { WorkbenchEntry } from './chat-workbench.service';
import {
  escalateHintForEntry,
  extractEntryErrorText,
  shouldShowErrorDetail,
} from './workbench-error.util';

function errEntry(partial: Partial<WorkbenchEntry>): WorkbenchEntry {
  return {
    id: 'e1',
    ts: 1,
    kind: 'tool',
    lane: 'chat',
    title: 'Edit file',
    status: 'error',
    ...partial,
  };
}

describe('workbench-error.util', () => {
  it('prefers Error chip over subtitle and detail', () => {
    const entry = errEntry({
      chips: [{ label: 'Error', value: 'chip error', variant: 'block' }],
      subtitle: 'subtitle error',
      detail: 'detail error',
    });
    expect(extractEntryErrorText(entry)).toBe('chip error');
  });

  it('hides full output when it duplicates inline error', () => {
    const text = 'PATH NOT IN YOUR ASSIGNMENT: foo.ts';
    const entry = errEntry({
      chips: [{ label: 'Error', value: text, variant: 'block' }],
      detail: text,
    });
    expect(shouldShowErrorDetail(entry, text)).toBe(false);
  });

  it('shows escalate hint for sub-agent file scope errors', () => {
    const text =
      'PATH NOT IN YOUR ASSIGNMENT: frontend/src/store/index.js is outside your wave-5 file scope.';
    const entry = errEntry({ delegateNumber: 7 });
    expect(escalateHintForEntry(entry, text)).toContain('escalate(reason');
    expect(escalateHintForEntry(entry, text)).toContain('file_access');
  });

  it('skips escalate hint for orchestrator errors', () => {
    const text = 'PATH NOT IN YOUR ASSIGNMENT: foo.ts';
    expect(escalateHintForEntry(errEntry({ delegateNumber: undefined }), text)).toBeNull();
  });
});
