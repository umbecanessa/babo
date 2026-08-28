import type { WorkbenchEntry } from './chat-workbench.service';

export type WorkbenchDensity = 'focused' | 'standard' | 'debug';

const READ_TOOLS = new Set(['read', 'grep', 'glob', 'list']);
const QUIET_ACTIVITY = /^(checking|crunching|waiting|polling)/i;

function isErrorEntry(e: WorkbenchEntry): boolean {
  return e.status === 'error';
}

function isWriteLike(e: WorkbenchEntry): boolean {
  const label = (e.toolLabel || '').toLowerCase();
  return label === 'write' || label === 'edit' || label === 'delete' || label === 'move';
}

function isLifecycle(e: WorkbenchEntry): boolean {
  const label = (e.toolLabel || '').toLowerCase();
  return (
    label === 'plan'
    || label === 'team'
    || label === 'delegate'
    || label === 'task'
    || label === 'mode'
    || label === 'comms'
  );
}

function isInternalNoise(e: WorkbenchEntry): boolean {
  const label = (e.toolLabel || '').toLowerCase();
  if (label === 'delegate' && (e.title || '').includes('/')) {
    return true;
  }
  if (label === 'activity' && QUIET_ACTIVITY.test(e.title || '')) {
    return true;
  }
  if (label === 'step' && (e.subtitle || '').includes('Thinking')) {
    return true;
  }
  return false;
}

/** Whether an entry should appear at the given density level. */
export function shouldShowWorkbenchEntry(
  e: WorkbenchEntry,
  density: WorkbenchDensity,
): boolean {
  if (density === 'debug') {
    return true;
  }
  if (isErrorEntry(e)) {
    return true;
  }
  if (isLifecycle(e)) {
    return true;
  }
  if (isWriteLike(e)) {
    return true;
  }
  const label = (e.toolLabel || '').toLowerCase();
  if (label === 'bash') {
    return true;
  }
  if (density === 'focused') {
    if (isInternalNoise(e)) {
      return false;
    }
    if (READ_TOOLS.has(label) && e.status === 'ok') {
      return false;
    }
    if (label === 'activity' && e.status === 'running') {
      return false;
    }
    if (label === 'comms' && e.status === 'ok') {
      return false;
    }
    return false;
  }
  return !isInternalNoise(e);
}

export function filterWorkbenchEntries(
  entries: WorkbenchEntry[],
  density: WorkbenchDensity,
): WorkbenchEntry[] {
  return entries.filter((e) => shouldShowWorkbenchEntry(e, density));
}

export const WORKBENCH_DENSITY_LABELS: Record<WorkbenchDensity, string> = {
  focused: 'Focused',
  standard: 'Standard',
  debug: 'Debug',
};
