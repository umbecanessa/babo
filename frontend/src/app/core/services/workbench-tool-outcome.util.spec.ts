import {
  resolveToolDisplayOutcome,
  toolDoneLabel,
} from './workbench-tool-outcome.util';

describe('workbench-tool-outcome.util', () => {
  it('keeps hard failures as error', () => {
    expect(
      resolveToolDisplayOutcome(
        true,
        'STALE FILE: /tmp/foo.py was modified since you last read it',
        'write',
      ),
    ).toBe('error');
    expect(
      resolveToolDisplayOutcome(true, 'Error: path required', 'write'),
    ).toBe('error');
  });

  it('downgrades successful write with policy warning to warn', () => {
    const preview =
      'Successfully wrote 53 lines (1506 bytes) to backend/main.py.\n\n' +
      '⚠ REPEATED WRITE: You have now written this same file 2 times';
    expect(resolveToolDisplayOutcome(true, preview, 'write')).toBe('warn');
  });

  it('downgrades successful edit with warning to warn', () => {
    expect(
      resolveToolDisplayOutcome(
        true,
        'Successfully edited backend/main.py.\n\n⚠ note',
        'edit',
      ),
    ).toBe('warn');
  });

  it('maps warn write label without failed wording', () => {
    expect(toolDoneLabel('write', 'warn')).toBe('File written (warning)');
    expect(toolDoneLabel('write', 'error')).toBe('Failed to write file');
    expect(toolDoneLabel('write', 'ok')).toBe('File written');
  });
});
