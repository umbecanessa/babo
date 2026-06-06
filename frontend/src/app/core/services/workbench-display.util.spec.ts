import {
  bucketsForParallelGroup,
  groupWorkbenchEntries,
  isRedundantStepSummary,
  parallelGroupTitle,
} from './workbench-display.util';
import type { WorkbenchEntry } from './chat-workbench.service';

function tool(partial: Partial<WorkbenchEntry>): WorkbenchEntry {
  return {
    id: partial.id || 't1',
    ts: partial.ts ?? 1,
    kind: 'tool',
    lane: 'chat',
    title: partial.title || 'Write file',
    toolLabel: partial.toolLabel || 'Write',
    ...partial,
  };
}

describe('workbench-display.util', () => {
  it('flags redundant step summaries', () => {
    expect(
      isRedundantStepSummary({
        id: 's',
        ts: 1,
        kind: 'agentic',
        lane: 'chat',
        title: 'Step 2/40',
        toolLabel: 'Step',
        subtitle: 'Write file · Read file (2/2 ok)',
      }),
    ).toBe(true);
  });

  it('groups parallel tools by groupKey', () => {
    const entries = [
      tool({ id: 'a', groupKey: 'run-1-step-2', ts: 10 }),
      tool({ id: 'b', groupKey: 'run-1-step-2', ts: 11 }),
      tool({ id: 'c', ts: 5, title: 'Plan: read' }),
    ];
    const items = groupWorkbenchEntries(entries);
    expect(items.length).toBe(2);
    expect(items[0].type).toBe('group');
    if (items[0].type === 'group') {
      expect(items[0].entries.length).toBe(2);
      expect(parallelGroupTitle(items[0].entries)).toBe('2× Write');
    }
  });

  it('does not merge parallel groups from different runs sharing step number', () => {
    const entries = [
      tool({ id: 'old-read', groupKey: 'run-1-step-1', toolLabel: 'Read', ts: 10 }),
      tool({ id: 'new-inspect', groupKey: 'run-2-step-1', toolLabel: 'Inspect', ts: 11 }),
    ];
    const items = groupWorkbenchEntries(entries);
    expect(items.length).toBe(2);
    expect(items.every((i) => i.type === 'single')).toBe(true);
  });

  it('buckets parallel tools by tool label', () => {
    const entries = [
      tool({ id: 'w1', toolLabel: 'Write', filePaths: ['a.ts'] }),
      tool({ id: 'w2', toolLabel: 'Write', filePaths: ['b.ts'] }),
      tool({ id: 'r1', toolLabel: 'Read', filePaths: ['c.ts'] }),
    ];
    const buckets = bucketsForParallelGroup(entries);
    expect(buckets.length).toBe(2);
    expect(buckets[0].toolLabel).toBe('WRITE');
    expect(buckets[0].entries.length).toBe(2);
    expect(buckets[1].toolLabel).toBe('READ');
    expect(buckets[1].entries.length).toBe(1);
  });
});
