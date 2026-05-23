export interface TeamMember {
  delegate_number: number;
  step_id: string;
  task: string;
  status: 'pending' | 'running' | 'done' | 'failed' | 'cancelled';
  result_summary: string;
  kanban_task_id: string;
  iterations: number;
  tool_calls: number;
  elapsed_seconds: number;
}

export interface Team {
  id: string;
  name: string;
  plan_id: string;
  wave_index: number;
  status: 'created' | 'active' | 'paused' | 'completed' | 'partial' | 'failed' | 'blocked';
  mission: string;
  briefing: string;
  members: TeamMember[];
  batch_id: string;
  checkback_job: string;
  kanban_parent_id: string;
  results_log: any[];
  created_at: number;
  completed_at: number;
}

export interface TodoItem {
  id: string;
  list_id: string;
  title: string;
  description: string;
  priority: 'low' | 'normal' | 'high' | 'urgent';
  status: 'inbox' | 'queued' | 'in_progress' | 'done' | 'deferred';
  idle_eligible: boolean;
  source: 'user' | 'agent' | 'channel';
  tags: string[];
  notes: string;
  due_date: string;
  plan_id: string;
  created_at: number;
  updated_at: number;
  completed_at: number | null;
  team_id: string;
  plan_step_id: string;
  parent_id: string;
  depends_on: string[];
  delegate_number: number | null;
}

export interface PlanStepSummary {
  id: string;
  label: string;
  status: 'pending' | 'in_progress' | 'done' | 'skipped' | 'failed';
  depends_on: string[];
  delegatable: boolean;
}

export interface PlanSummary {
  id: string;
  title: string;
  status: string;
  progress: string;
  steps: PlanStepSummary[];
}

export interface TodoList {
  id: string;
  name: string;
  icon: string;
  description: string;
  color: string;
  sort_order: number;
}

export interface TimelineWave {
  wave_index: number;
  steps: PlanStepSummary[];
  team: {
    id: string;
    name: string;
    status: string;
    progress: string;
    created_at: number;
    completed_at: number;
  } | null;
}

export interface Timeline {
  plan_id: string;
  title: string;
  status: string;
  waves: TimelineWave[];
}

export interface CommandResponse {
  status: string;
  response: string;
  iterations: number;
}
