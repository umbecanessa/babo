import type { WorkbenchEntry } from './chat-workbench.service';
import type { WorkbenchDensity } from './workbench-density.util';
import { shouldShowWorkbenchEntry } from './workbench-density.util';

export type WorkbenchDisplaySingle = { type: 'single'; entry: WorkbenchEntry };
export type WorkbenchDisplayGroup = {
  type: 'group';
  id: string;
  entries: WorkbenchEntry[];
  lane: WorkbenchEntry['lane'];
  delegateNumber?: number;
  ts: number;
  status: 'running' | 'ok' | 'error';
};
export type WorkbenchDisplayItem = WorkbenchDisplaySingle | WorkbenchDisplayGroup;

/** Drop per-iteration step summaries when tools are listed separately. */
export function isRedundantStepSummary(entry: WorkbenchEntry): boolean {
  return (
    entry.kind === 'agentic'
    && entry.toolLabel === 'Step'
    && !!(entry.subtitle || '').includes('·')
  );
}

export function groupWorkbenchEntries(
  entries: WorkbenchEntry[],
  density: WorkbenchDensity = 'debug',
): WorkbenchDisplayItem[] {
  const filtered = entries.filter((e) => !isRedundantStepSummary(e));
  const groups = new Map<string, WorkbenchEntry[]>();
  const ungrouped: WorkbenchEntry[] = [];

  for (const entry of filtered) {
    const key = entry.groupKey?.trim();
    if (key && entry.kind === 'tool') {
      const bucket = groups.get(key) ?? [];
      bucket.push(entry);
      groups.set(key, bucket);
    } else {
      ungrouped.push(entry);
    }
  }

  const sortTools = (a: WorkbenchEntry, b: WorkbenchEntry) => b.ts - a.ts;
  const ranked: Array<{ ts: number; item: WorkbenchDisplayItem }> = [];

  for (const entry of ungrouped) {
    ranked.push({ ts: entry.ts, item: { type: 'single', entry } });
  }

  for (const [id, bucket] of groups) {
    const visible = bucket.filter((e) => shouldShowWorkbenchEntry(e, density));
    if (!visible.length) {
      continue;
    }
    const sorted = [...visible].sort(sortTools);
    if (sorted.length === 1) {
      ranked.push({ ts: sorted[0].ts, item: { type: 'single', entry: sorted[0] } });
      continue;
    }
    const status = sorted.some((e) => e.status === 'error')
      ? 'error'
      : sorted.some((e) => e.status === 'running')
        ? 'running'
        : 'ok';
    ranked.push({
      ts: Math.max(...sorted.map((e) => e.ts)),
      item: {
        type: 'group',
        id,
        entries: sorted,
        lane: sorted[0].lane,
        delegateNumber: sorted[0].delegateNumber,
        ts: Math.max(...sorted.map((e) => e.ts)),
        status,
      },
    });
  }

  return ranked.sort((a, b) => b.ts - a.ts).map((r) => r.item);
}

export type WorkbenchToolBucket = {
  toolLabel: string;
  entries: WorkbenchEntry[];
  status: 'running' | 'ok' | 'error';
};

/** Group parallel tool rows by badge (Write, Read, …) for compact UI. */
export function bucketsForParallelGroup(
  entries: WorkbenchEntry[],
): WorkbenchToolBucket[] {
  const order: string[] = [];
  const map = new Map<string, WorkbenchEntry[]>();
  for (const entry of entries) {
    const label = (entry.toolLabel || entry.title || 'Tool').toUpperCase();
    if (!map.has(label)) {
      map.set(label, []);
      order.push(label);
    }
    map.get(label)!.push(entry);
  }
  return order.map((toolLabel) => {
    const bucket = map.get(toolLabel)!;
    const status = bucket.some((e) => e.status === 'error')
      ? 'error'
      : bucket.some((e) => e.status === 'running')
        ? 'running'
        : 'ok';
    return { toolLabel, entries: bucket, status };
  });
}

export function parallelGroupTitle(entries: WorkbenchEntry[]): string {
  const labels = entries.map((e) => e.toolLabel || e.title || 'Tool');
  const unique = [...new Set(labels)];
  if (unique.length === 1) {
    return `${entries.length}× ${unique[0]}`;
  }
  const counts = new Map<string, number>();
  for (const label of labels) {
    counts.set(label, (counts.get(label) || 0) + 1);
  }
  const parts = [...counts.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 4)
    .map(([label, n]) => (n > 1 ? `${n}× ${label}` : label));
  const extra = counts.size > 4 ? ` +${counts.size - 4} more` : '';
  return parts.join(', ') + extra;
}

export function parseModeTransition(title: string): { from: string; to: string } | null {
  const m = (title || '').match(/^(.+?)\s*→\s*(.+)$/);
  if (!m) return null;
  return { from: m[1].trim(), to: m[2].trim() };
}
