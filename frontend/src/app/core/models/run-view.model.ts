/** Live orchestration run — plan steps with nested delegates (shared Chat + Projects). */

export type RunStepStatus = 'pending' | 'active' | 'done' | 'error' | 'skipped';

export type RunDelegateStatus = 'queued' | 'running' | 'done' | 'error';

export interface RunDelegateToolCall {
  name: string;
  args?: Record<string, unknown>;
  callId?: string;
  result?: 'done' | 'error' | 'running';
  isError?: boolean;
}

export interface RunDelegate {
  number: number;
  /** Stable key for queued members before delegate_number is assigned. */
  memberKey?: string;
  task: string;
  status: RunDelegateStatus;
  stepId: string;
  teamId?: string;
  teamName?: string;
  waveAttempt?: number;
  memberIdx?: number;
  maxIterations?: number;
  iterations?: number;
  totalToolCalls?: number;
  summary?: string;
  toolCalls: RunDelegateToolCall[];
  expanded: boolean;
}

export interface RunStep {
  id: string;
  label: string;
  status: RunStepStatus;
  delegatable: boolean;
  detail?: string;
  delegates: RunDelegate[];
}

export interface RunWave {
  teamId: string;
  name: string;
  waveIndex: number;
  waveAttempt: number;
  status: string;
  doneCount: number;
  totalCount: number;
  runningCount: number;
}

export interface RunViewSnapshot {
  planId: string;
  title: string;
  todoId?: string;
  steps: RunStep[];
  waves: RunWave[];
  expanded: boolean;
  background: boolean;
  archived?: boolean;
  unassignedDelegates: RunDelegate[];
}

export interface RunViewPersisted {
  snapshot: RunViewSnapshot;
  activeDelegateNumbers: number[];
}

/** Compact row shown on the collapsed Run panel bar. */
export interface RunCollapsedActivity {
  key: string;
  label: string;
  status: 'running' | 'queued';
}
