export interface TodoItem {
  id: string;
  list_id: string;
  title: string;
  description: string;
  priority: 'low' | 'normal' | 'high' | 'urgent';
  status: 'inbox' | 'queued' | 'in_progress' | 'blocked' | 'done' | 'deferred';
  idle_eligible: boolean;
  source: 'user' | 'agent' | 'channel';
  tags: string[];
  notes: string;
  due_date: string;
  plan_id: string;
  created_at: number;
  updated_at: number;
  completed_at: number | null;
  parent_id?: string;
  team_id?: string;
  delegate_number?: number | null;
}

export interface PlanStepSummary {
  id: string;
  label: string;
  status: 'pending' | 'in_progress' | 'done' | 'skipped' | 'failed';
  notes: string;
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
