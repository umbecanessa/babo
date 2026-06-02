import { Injectable, computed, inject, signal } from '@angular/core';
import { AgentWorkspaceContextService } from './agent-workspace-context.service';
import type { PlanSummary, Team, TeamMember } from '../../features/projects/project.models';
import type {
  RunCollapsedActivity,
  RunDelegate,
  RunDelegateToolCall,
  RunStep,
  RunStepStatus,
  RunViewPersisted,
  RunViewSnapshot,
  RunWave,
} from '../models/run-view.model';

const CHAT_TOOL_PREVIEW = 3;
const PROJECTS_TOOL_PREVIEW = 12;

function mapPlanStatus(raw: string | undefined): RunStepStatus {
  const s = (raw || 'pending').toLowerCase();
  if (s === 'done') return 'done';
  if (s === 'failed') return 'error';
  if (s === 'skipped') return 'skipped';
  // Plan in_progress means "scheduled / started in ledger" — UI active state
  // comes only from live delegates (_syncStepStatusFromDelegates).
  if (s === 'active') return 'active';
  return 'pending';
}

function mapMemberStatus(raw: string | undefined): RunDelegate['status'] {
  const s = (raw || 'pending').toLowerCase();
  if (s === 'done') return 'done';
  if (s === 'failed' || s === 'cancelled') return 'error';
  if (s === 'pending') return 'queued';
  return 'running';
}

function memberKey(teamId: string, memberIdx: number): string {
  return `${teamId}:m${memberIdx}`;
}

function delegateTrackKey(d: RunDelegate): string {
  if (d.memberKey) return d.memberKey;
  if (d.number >= 0) return `n:${d.number}`;
  if (d.teamId && d.memberIdx != null) return memberKey(d.teamId, d.memberIdx);
  return `s:${d.stepId}:${d.task.slice(0, 48)}`;
}

/** Same logical delegate slot (WS events vs team roster may use different keys). */
function sameDelegateSlot(a: RunDelegate, b: RunDelegate): boolean {
  if (a.memberKey && b.memberKey && a.memberKey === b.memberKey) return true;
  if (delegateTrackKey(a) === delegateTrackKey(b)) return true;
  if (a.number >= 0 && a.number === b.number) {
    if (a.stepId && b.stepId) return a.stepId === b.stepId;
    if (a.teamId && b.teamId) return a.teamId === b.teamId;
    return true;
  }
  return false;
}

function mergeDelegateRecords(a: RunDelegate, b: RunDelegate): RunDelegate {
  return {
    ...a,
    ...b,
    number: b.number >= 0 ? b.number : a.number,
    memberKey: b.memberKey || a.memberKey,
    stepId: b.stepId || a.stepId,
    teamId: b.teamId || a.teamId,
    toolCalls: b.toolCalls.length ? b.toolCalls : a.toolCalls,
    expanded: b.expanded ?? a.expanded,
  };
}

function dedupeDelegateList(delegates: RunDelegate[]): RunDelegate[] {
  const out: RunDelegate[] = [];
  for (const d of delegates) {
    const i = out.findIndex(x => sameDelegateSlot(x, d));
    if (i >= 0) {
      out[i] = mergeDelegateRecords(out[i], d);
    } else {
      out.push(d);
    }
  }
  return out;
}

function taskTitle(task: string): string {
  const line = (task || '').split('\n').find(l => l.trim()) || task;
  return line.length > 120 ? line.slice(0, 117) + '…' : line;
}

@Injectable({ providedIn: 'root' })
export class RunViewService {
  private readonly workspaceCtx = inject(AgentWorkspaceContextService);
  private agentId = '';
  private readonly _planId = signal('');
  private readonly _title = signal('');
  private readonly _todoId = signal<string | undefined>(undefined);
  private readonly _steps = signal<RunStep[]>([]);
  private readonly _waves = signal<RunWave[]>([]);
  private readonly _expanded = signal(false);
  private readonly _background = signal(false);
  private readonly _archived = signal(false);
  private readonly _recoveryPending = signal(false);
  private readonly _unassigned = signal<RunDelegate[]>([]);
  private readonly _activeDelegates = new Set<number>();
  private readonly _delegateEventBuffer = new Map<number, unknown[]>();
  private readonly _teamsById = new Map<string, Team>();

  readonly planId = this._planId.asReadonly();
  readonly title = this._title.asReadonly();
  readonly expanded = this._expanded.asReadonly();
  readonly background = this._background.asReadonly();
  readonly archived = this._archived.asReadonly();
  readonly recoveryPending = this._recoveryPending.asReadonly();

  readonly needsWrapUp = computed(() => {
    if (this._archived() || this._steps().length === 0) return false;
    const total = this.totalCount();
    const done = this.doneCount();
    return total > 0 && done === total && !this.isLive();
  });

  readonly visible = computed(() => this._steps().length > 0 || this._unassigned().length > 0);

  readonly doneCount = computed(() =>
    this._steps().filter(s => s.status === 'done').length,
  );

  readonly skippedCount = computed(() =>
    this._steps().filter(s => s.status === 'skipped').length,
  );

  readonly totalCount = computed(() => this._steps().length);

  readonly runningDelegateCount = computed(() => {
    let n = 0;
    for (const s of this._steps()) {
      n += s.delegates.filter(d => d.status === 'running' || d.status === 'queued').length;
    }
    n += this._unassigned().filter(d => d.status === 'running' || d.status === 'queued').length;
    return n;
  });

  readonly isLive = computed(() => {
    if (this._archived()) return false;
    if (this.runningDelegateCount() > 0) return true;
    return this._steps().some(s => s.status === 'active');
  });

  readonly collapsedActivity = computed((): RunCollapsedActivity[] => {
    const items: RunCollapsedActivity[] = [];
    const seen = new Set<string>();
    for (const step of this._steps()) {
      for (const d of step.delegates) {
        if (d.status !== 'running' && d.status !== 'queued') continue;
        const key = delegateTrackKey(d);
        if (seen.has(key)) continue;
        seen.add(key);
        items.push({
          key,
          label: step.label || taskTitle(d.task),
          status: d.status,
        });
      }
    }
    for (const d of this._unassigned()) {
      if (d.status !== 'running' && d.status !== 'queued') continue;
      const key = delegateTrackKey(d);
      if (seen.has(key)) continue;
      seen.add(key);
      items.push({
        key,
        label: taskTitle(d.task),
        status: d.status,
      });
    }
    return items;
  });

  readonly activeWaveLabel = computed(() => {
    const waves = this._waves().filter(w =>
      ['active', 'created', 'paused', 'partial'].includes(w.status),
    );
    if (waves.length === 0) return '';
    const w = waves[waves.length - 1];
    const attempt = w.waveAttempt > 1 ? ` · try ${w.waveAttempt}` : '';
    return `${w.name}${attempt}`;
  });

  readonly headerSummary = computed(() => {
    const done = this.doneCount();
    const skipped = this.skippedCount();
    const total = this.totalCount();
    const running = this.runningDelegateCount();
    const wave = this.activeWaveLabel();
    const parts: string[] = [];
    if (total > 0) {
      parts.push(`${done}/${total} done`);
      if (skipped > 0) parts.push(`${skipped} skipped`);
    }
    if (running > 0) parts.push(`${running} working`);
    if (wave) parts.push(wave);
    return parts.join(' · ');
  });

  readonly snapshot = computed((): RunViewSnapshot => ({
    planId: this._planId(),
    title: this._title(),
    todoId: this._todoId(),
    steps: this._steps(),
    waves: this._waves(),
    expanded: this._expanded(),
    background: this._background(),
    archived: this._archived(),
    unassignedDelegates: this._unassigned(),
  }));

  steps = this._steps.asReadonly();
  waves = this._waves.asReadonly();
  unassignedDelegates = this._unassigned.asReadonly();

  bindAgent(agentId: string): void {
    if (this.agentId === agentId) return;
    this.agentId = agentId;
    this.clear();
  }

  clear(): void {
    this._planId.set('');
    this._title.set('');
    this._todoId.set(undefined);
    this._steps.set([]);
    this._waves.set([]);
    this._unassigned.set([]);
    this._expanded.set(false);
    this._background.set(false);
    this._archived.set(false);
    this._recoveryPending.set(false);
    this._activeDelegates.clear();
    this._delegateEventBuffer.clear();
    this._teamsById.clear();
  }

  toggleExpanded(): void {
    this._expanded.update(v => !v);
  }

  setExpanded(expanded: boolean): void {
    this._expanded.set(expanded);
  }

  toggleDelegateExpanded(delegateId: number | string): void {
    if (typeof delegateId === 'number') {
      this._mutateDelegate(delegateId, d => ({ ...d, expanded: !d.expanded }));
      return;
    }
    const all = this._allDelegates();
    const idx = all.findIndex(d => delegateTrackKey(d) === delegateId || d.memberKey === delegateId);
    if (idx < 0) return;
    all[idx] = { ...all[idx], expanded: !all[idx].expanded };
    this._steps.set(this._syncStepStatusFromDelegates(
      this._reconcileDelegatesIntoSteps(this._steps(), all),
    ));
  }

  toolPreviewLimit(mode: 'chat' | 'projects'): number {
    return mode === 'projects' ? PROJECTS_TOOL_PREVIEW : CHAT_TOOL_PREVIEW;
  }

  restorePersisted(data: RunViewPersisted | null | undefined): void {
    if (!data?.snapshot) return;
    const snap = data.snapshot;
    this._planId.set(snap.planId || '');
    this._title.set(snap.title || '');
    this._todoId.set(snap.todoId);
    this._steps.set(structuredClone(snap.steps || []));
    this._waves.set(structuredClone(snap.waves || []));
    this._expanded.set(!!snap.expanded);
    this._background.set(!!snap.background);
    this._archived.set(!!snap.archived);
    this._unassigned.set(structuredClone(snap.unassignedDelegates || []));
    this._activeDelegates.clear();
    for (const n of data.activeDelegateNumbers || []) {
      if (typeof n === 'number') this._activeDelegates.add(n);
    }
  }

  persisted(): RunViewPersisted {
    return {
      snapshot: this.snapshot(),
      activeDelegateNumbers: Array.from(this._activeDelegates),
    };
  }

  /** Mark the current run archived (plan deleted / superseded). */
  markPlanArchived(planId?: string): void {
    const current = this._planId();
    if (planId && current && planId !== current) return;

    this._archived.set(true);
    this._background.set(false);
    this._waves.set([]);
    this._unassigned.set([]);
    this._activeDelegates.clear();
    this._delegateEventBuffer.clear();
    this._teamsById.clear();

    const title = this._title();
    if (title && !title.toLowerCase().startsWith('archived')) {
      this._title.set(`Archived · ${title}`);
    }

    this._steps.update(steps =>
      steps.map(s => ({
        ...s,
        status: 'skipped' as RunStepStatus,
        delegates: s.delegates.map(d => ({
          ...d,
          status: 'error' as RunDelegate['status'],
        })),
      })),
    );
  }

  /** Merge authoritative plan from REST (Projects load / todo plan). */
  hydratePlan(plan: PlanSummary, todoId?: string): void {
    if (!plan?.steps?.length) return;
    this._archived.set(false);
    this._planId.set(plan.id || '');
    this._title.set(plan.title || 'Project plan');
    if (todoId) this._todoId.set(todoId);

    const existing = new Map(this._steps().map(s => [s.id, s]));
    const steps: RunStep[] = plan.steps.map((ps, idx) => {
      const id = ps.id || `step-${idx + 1}`;
      const prev = existing.get(id);
      return {
        id,
        label: ps.label || `Step ${idx + 1}`,
        status: mapPlanStatus(ps.status),
        delegatable: ps.delegatable !== false,
        partialAccept: prev?.partialAccept || (ps.notes || '').includes('[accept_partial]'),
        detail: prev?.detail,
        delegates: prev?.delegates?.length ? [...prev.delegates] : [],
      };
    });
    this._steps.set(this._syncStepStatusFromDelegates(
      this._reconcileDelegatesIntoSteps(steps, this._allDelegates()),
    ));
  }

  /** Merge team roster from REST or WS — attaches members to plan steps. */
  hydrateTeams(teams: Team[]): void {
    for (const t of teams) this._teamsById.set(t.id, t);
    this._waves.set(this._buildWaves(teams));
    const delegates = this._delegatesFromTeams(teams);
    this._steps.update(steps =>
      this._syncStepStatusFromDelegates(this._reconcileDelegatesIntoSteps(steps, delegates)),
    );
  }

  handleMessage(msg: Record<string, unknown>): boolean {
    if (!msg?.['type']) return false;
    const type = String(msg['type']);

    switch (type) {
      case 'agentic_plan':
        this._onAgenticPlan(msg);
        return true;
      case 'plan_step_update':
        this._onPlanStepUpdate(msg);
        return true;
      case 'delegate_start':
        this._onDelegateStart(msg);
        return true;
      case 'delegate_end':
        this._onDelegateEnd(msg);
        return true;
      case 'delegate_progress':
        this._onDelegateProgress(msg);
        return true;
      case 'tool_execution_start':
        if (this._onDelegateToolStart(msg)) return true;
        return false;
      case 'tool_execution_end':
        if (this._onPlanToolEnd(msg)) return true;
        if (this._onDelegateToolEnd(msg)) return true;
        return false;
      case 'tool_output_chunk':
        if (this._onDelegateToolChunk(msg)) return true;
        return false;
      case 'team_created':
      case 'team_launched':
      case 'team_advanced':
      case 'team_paused':
      case 'team_resumed':
      case 'team_disbanded':
      case 'team_complete':
      case 'team_member_complete':
      case 'team_member_progress':
      case 'team_member_spawned':
        this._onTeamPayload(msg);
        return true;
      default:
        return false;
    }
  }

  private _onAgenticPlan(msg: Record<string, unknown>): void {
    const rawSteps = (msg['steps'] as unknown[]) || [];
    if (rawSteps.length === 0) return;

    const planId = String(msg['plan_id'] || this._planId() || '');
    const title = String(msg['title'] || this._title() || 'Current run');
    const todoId = msg['todo_id'] != null ? String(msg['todo_id']) : this._todoId();

    if (planId) this._planId.set(planId);
    this._title.set(title.replace(/^Archived · /i, ''));
    this._archived.set(false);
    if (todoId) this._todoId.set(todoId);
    if (msg['project_dir']) {
      this.workspaceCtx.setProjectDir(this.agentId, String(msg['project_dir']));
    }
    if (msg['autonomous'] === true) this._background.set(true);

    const hasIds = rawSteps.some(s => typeof s === 'object' && s !== null && (s as { id?: string }).id);
    const parsed: RunStep[] = rawSteps.map((s, idx) => {
      if (typeof s === 'string') {
        return {
          id: `step-${idx + 1}`,
          label: s,
          status: 'pending' as RunStepStatus,
          delegatable: true,
          delegates: [],
        };
      }
      const o = s as { id?: string; label?: string; status?: string; delegatable?: boolean };
      return {
        id: o.id || `step-${idx + 1}`,
        label: o.label || '',
        status: mapPlanStatus(o.status),
        delegatable: o.delegatable !== false,
        delegates: [],
      };
    });

    const merged = this._reconcileDelegatesIntoSteps(parsed, this._allDelegates());
    this._steps.set(merged);
    this._waves.set([]);
    this._unassigned.set([]);
    this._teamsById.clear();
    if (!msg['autonomous']) {
      this._expanded.set(true);
      this._background.set(false);
    }
  }

  private _onPlanStepUpdate(msg: Record<string, unknown>): void {
    const stepIdx = Number(msg['step_index'] ?? -1);
    const rawStatus = String(msg['status'] || 'done');
    const status = mapPlanStatus(rawStatus);
    const stepId = msg['step_id'] != null ? String(msg['step_id']) : '';

    this._steps.update(steps => {
      const copy = [...steps];
      let idx = stepIdx;
      if (stepId) {
        const byId = copy.findIndex(s => s.id === stepId);
        if (byId >= 0) idx = byId;
      }
      if (idx < 0 || idx >= copy.length) return steps;
      const prev = copy[idx];
      copy[idx] = {
        ...prev,
        status,
        ...(status === 'done'
          ? {
              delegates: prev.delegates.map(d =>
                d.status === 'error' ? { ...d, status: 'done' as const } : d,
              ),
            }
          : {}),
      };
      return this._syncStepStatusFromDelegates(copy);
    });

    if (msg['autonomous'] === true) this._background.set(true);
  }

  private _onDelegateStart(msg: Record<string, unknown>): void {
    const num = Number(msg['delegate_number'] ?? -1);
    if (num < 0) return;

    const teamId = msg['team_id'] != null ? String(msg['team_id']) : '';
    const memberIdx = msg['member_idx'] != null ? Number(msg['member_idx']) : undefined;
    if (teamId && memberIdx != null) {
      this._removeDelegateByKey(memberKey(teamId, memberIdx));
    }

    const delegate: RunDelegate = {
      number: num,
      memberKey: teamId && memberIdx != null ? memberKey(teamId, memberIdx) : undefined,
      task: String(msg['delegate_task'] || 'Sub-task'),
      status: 'running',
      stepId: String(msg['step_id'] || ''),
      teamId: teamId || undefined,
      teamName: msg['team_name'] != null ? String(msg['team_name']) : undefined,
      waveAttempt: msg['wave_attempt'] != null ? Number(msg['wave_attempt']) : undefined,
      memberIdx,
      maxIterations: msg['max_steps'] != null ? Number(msg['max_steps']) : undefined,
      toolCalls: [],
      expanded: true,
    };

    this._upsertDelegate(delegate);
    this._activeDelegates.add(num);
    if (msg['autonomous'] === true) this._background.set(true);

    const buffered = this._delegateEventBuffer.get(num) || [];
    this._delegateEventBuffer.delete(num);
    for (const b of buffered) {
      this.handleMessage(b as Record<string, unknown>);
    }
  }

  private _onDelegateProgress(msg: Record<string, unknown>): void {
    const num = Number(msg['delegate_number'] ?? -1);
    if (num < 0) return;
    this._mutateDelegate(num, d => ({
      ...d,
      iterations: Number(msg['iteration'] ?? d.iterations ?? 0),
      maxIterations: Number(msg['max_iterations'] ?? d.maxIterations ?? 0),
    }));
  }

  private _onDelegateEnd(msg: Record<string, unknown>): void {
    const num = Number(msg['delegate_number'] ?? -1);
    if (num < 0) return;
    const aborted = !!msg['aborted'];
    this._mutateDelegate(num, d => ({
      ...d,
      status: aborted ? 'error' : 'done',
      summary: String(msg['summary'] || d.summary || ''),
      iterations: Number(msg['iterations'] ?? d.iterations ?? 0),
      totalToolCalls: Number(msg['tool_calls'] ?? d.totalToolCalls ?? d.toolCalls.length),
      expanded: d.expanded ?? false,
      toolCalls: aborted
        ? d.toolCalls.map(tc => (tc.result ? tc : { ...tc, result: 'error' as const, isError: true }))
        : d.toolCalls,
    }));
    this._activeDelegates.delete(num);
  }

  private _onDelegateToolStart(msg: Record<string, unknown>): boolean {
    if (msg['sub_agent'] !== true) return false;
    const num = Number(msg['delegate_number'] ?? -1);
    if (num < 0) return false;
    if (!this._activeDelegates.has(num) && !this._findDelegate(num)) {
      const buf = this._delegateEventBuffer.get(num) || [];
      buf.push(msg);
      this._delegateEventBuffer.set(num, buf);
      return true;
    }
    const toolName = String(msg['tool_name'] || 'tool');
    const args = (msg['arguments'] as Record<string, unknown>) || {};
    const callId = msg['call_id'] != null ? String(msg['call_id']) : '';
    const tc: RunDelegateToolCall = { name: toolName, args, callId, result: 'running' };
    this._mutateDelegate(num, d => ({
      ...d,
      toolCalls: [...d.toolCalls, tc],
    }));
    return true;
  }

  private _onPlanToolEnd(msg: Record<string, unknown>): boolean {
    if (msg['sub_agent'] === true) return false;
    if (String(msg['tool_name'] || '') !== 'plan') return false;
    if (msg['is_error']) return true;
    const details = msg['details'] as Record<string, unknown> | undefined;
    const action = String(details?.['action'] || '');
    if (action === 'delete') {
      const planId = details?.['plan_id'] != null ? String(details['plan_id']) : undefined;
      this.markPlanArchived(planId);
      return true;
    }
    if (action === 'accept_partial') {
      const stepId = details?.['step_id'] != null ? String(details['step_id']) : '';
      this._markStepAccepted(stepId, true);
      this._recoveryPending.set(true);
      return true;
    }
    if (action === 'complete') {
      this._recoveryPending.set(false);
      return true;
    }
    return false;
  }

  private _markStepAccepted(stepId: string, partial: boolean): void {
    if (!stepId) return;
    this._steps.update(steps => {
      const copy = steps.map(s => {
        if (s.id !== stepId) return s;
        return {
          ...s,
          status: 'done' as RunStepStatus,
          partialAccept: partial || s.partialAccept,
          detail: partial ? 'Partial work accepted — orchestrator can fix gaps or launch another wave.' : s.detail,
          delegates: s.delegates.map(d =>
            d.status === 'error' ? { ...d, status: 'done' as const } : d,
          ),
        };
      });
      return this._syncStepStatusFromDelegates(copy);
    });
  }

  private _onDelegateToolEnd(msg: Record<string, unknown>): boolean {
    if (msg['sub_agent'] !== true) return false;
    const num = Number(msg['delegate_number'] ?? -1);
    if (num < 0) return false;
    if (!this._findDelegate(num)) {
      const buf = this._delegateEventBuffer.get(num) || [];
      buf.push(msg);
      this._delegateEventBuffer.set(num, buf);
      return true;
    }
    const callId = msg['call_id'] != null ? String(msg['call_id']) : '';
    const isError = !!msg['is_error'];
    this._mutateDelegate(num, d => {
      const toolCalls = [...d.toolCalls];
      let match = callId ? toolCalls.findIndex(tc => tc.callId === callId) : -1;
      if (match < 0) {
        for (let i = toolCalls.length - 1; i >= 0; i--) {
          if (!toolCalls[i].result || toolCalls[i].result === 'running') {
            match = i;
            break;
          }
        }
      }
      if (match >= 0) {
        toolCalls[match] = {
          ...toolCalls[match],
          result: isError ? 'error' : 'done',
          isError,
        };
      }
      return { ...d, toolCalls };
    });
    return true;
  }

  private _onDelegateToolChunk(msg: Record<string, unknown>): boolean {
    if (msg['sub_agent'] !== true) return false;
    const num = Number(msg['delegate_number'] ?? -1);
    if (num < 0 || !this._findDelegate(num)) return false;
    return true;
  }

  private _onTeamPayload(msg: Record<string, unknown>): void {
    const team = msg['team'] as Team | undefined;
    if (!team?.id) return;
    this._teamsById.set(team.id, team);
    this.hydrateTeams(Array.from(this._teamsById.values()));

    const member = msg['member'] as TeamMember | undefined;
    if (member && member.delegate_number >= 0) {
      const existing = this._findDelegate(member.delegate_number);
      const status = mapMemberStatus(member.status);
      const delegate: RunDelegate = {
        number: member.delegate_number,
        task: member.task || existing?.task || '',
        status,
        stepId: member.step_id || existing?.stepId || '',
        teamId: team.id,
        teamName: team.name,
        waveAttempt: team.wave_attempt,
        memberIdx: team.members.findIndex(m => m.delegate_number === member.delegate_number),
        iterations: member.iterations,
        totalToolCalls: member.tool_calls,
        summary: member.result_summary || existing?.summary,
        toolCalls: existing?.toolCalls || [],
        expanded: existing?.expanded ?? (status === 'running'),
      };
      this._upsertDelegate(delegate);
      if (delegate.status === 'running') {
        this._activeDelegates.add(delegate.number);
      }
    }
  }

  private _buildWaves(teams: Team[]): RunWave[] {
    const byKey = new Map<string, RunWave>();
    for (const t of teams) {
      const members = t.members || [];
      const done = members.filter(m => m.status === 'done').length;
      const running = members.filter(m => m.status === 'running').length;
      const key = `${t.wave_index}:${t.wave_attempt ?? 1}`;
      const wave: RunWave = {
        teamId: t.id,
        name: t.name || `Wave ${t.wave_index + 1}`,
        waveIndex: t.wave_index ?? 0,
        waveAttempt: t.wave_attempt ?? 1,
        status: t.status,
        doneCount: done,
        totalCount: members.length,
        runningCount: running,
      };
      const prev = byKey.get(key);
      if (!prev || t.created_at > (teams.find(x => x.id === prev.teamId)?.created_at ?? 0)) {
        byKey.set(key, wave);
      }
    }
    return Array.from(byKey.values()).sort((a, b) =>
      a.waveIndex - b.waveIndex || a.waveAttempt - b.waveAttempt,
    );
  }

  private _delegatesFromTeams(teams: Team[]): RunDelegate[] {
    const out: RunDelegate[] = [];
    for (const t of teams) {
      for (let i = 0; i < (t.members || []).length; i++) {
        const m = t.members[i];
        const mKey = memberKey(t.id, i);
        const existing =
          this._findDelegateByKey(mKey)
          ?? (m.delegate_number >= 0 ? this._findDelegate(m.delegate_number) : undefined);
        const mapped = mapMemberStatus(m.status);
        const num = m.delegate_number >= 0 ? m.delegate_number : existing?.number ?? -1;
        out.push({
          number: num,
          memberKey: mKey,
          task: m.task || existing?.task || '',
          status: mapped,
          stepId: m.step_id || '',
          teamId: t.id,
          teamName: t.name,
          waveAttempt: t.wave_attempt,
          memberIdx: i,
          iterations: m.iterations,
          totalToolCalls: m.tool_calls,
          summary: m.result_summary || existing?.summary,
          toolCalls: existing?.toolCalls || [],
          expanded: existing?.expanded ?? (mapped === 'running' || mapped === 'queued'),
        });
      }
    }
    return out;
  }

  private _allDelegates(): RunDelegate[] {
    const map = new Map<string, RunDelegate>();
    const add = (d: RunDelegate) => map.set(delegateTrackKey(d), d);
    for (const d of this._unassigned()) add(d);
    for (const s of this._steps()) {
      for (const d of s.delegates) add(d);
    }
    return Array.from(map.values());
  }

  private _findDelegateByKey(key: string): RunDelegate | undefined {
    return this._allDelegates().find(d => delegateTrackKey(d) === key || d.memberKey === key);
  }

  private _removeDelegateByKey(key: string): void {
    const all = this._allDelegates().filter(d => delegateTrackKey(d) !== key && d.memberKey !== key);
    this._steps.set(this._syncStepStatusFromDelegates(
      this._reconcileDelegatesIntoSteps(this._steps(), all),
    ));
  }

  private _reconcileDelegatesIntoSteps(steps: RunStep[], delegates: RunDelegate[]): RunStep[] {
    const copy = steps.map(s => ({ ...s, delegates: [] as RunDelegate[] }));
    const unassigned: RunDelegate[] = [];

    for (const d of dedupeDelegateList(delegates)) {
      let stepId = d.stepId;
      if (!stepId && d.teamId) {
        const team = this._teamsById.get(d.teamId);
        const member = team?.members?.find(m => m.delegate_number === d.number);
        if (member?.step_id) stepId = member.step_id;
      }
      const idx = stepId ? copy.findIndex(s => s.id === stepId) : -1;
      if (idx >= 0) {
        const existIdx = copy[idx].delegates.findIndex(x => sameDelegateSlot(x, d));
        if (existIdx >= 0) {
          copy[idx].delegates[existIdx] = mergeDelegateRecords(
            copy[idx].delegates[existIdx],
            d,
          );
        } else {
          copy[idx].delegates.push(d);
        }
      } else {
        const activeIdx = copy.findIndex(s => s.status === 'active');
        if (activeIdx >= 0 && !stepId) {
          const existIdx = copy[activeIdx].delegates.findIndex(x => sameDelegateSlot(x, d));
          if (existIdx >= 0) {
            copy[activeIdx].delegates[existIdx] = mergeDelegateRecords(
              copy[activeIdx].delegates[existIdx],
              d,
            );
          } else {
            copy[activeIdx].delegates.push(d);
          }
        } else {
          unassigned.push(d);
        }
      }
    }

    this._unassigned.set(dedupeDelegateList(unassigned));
    return this._syncStepStatusFromDelegates(copy);
  }

  private _syncStepStatusFromDelegates(steps: RunStep[]): RunStep[] {
    return steps.map(s => {
      if (s.status === 'done' || s.status === 'skipped' || s.partialAccept) {
        return s.status === 'error' ? { ...s, status: 'done' as RunStepStatus } : s;
      }

      if (!s.delegates.length) {
        if (s.status === 'active') {
          return { ...s, status: 'pending' };
        }
        return s;
      }

      const live = s.delegates.filter(
        d => d.status === 'running' || d.status === 'queued',
      );
      const terminal = s.delegates.filter(
        d => d.status === 'done' || d.status === 'error',
      );

      if (live.length > 0) {
        if (s.status === 'pending') {
          return { ...s, status: 'active' };
        }
        return s;
      }

      if (terminal.length === s.delegates.length) {
        const allFailed = s.delegates.every(d => d.status === 'error');
        if (allFailed) {
          return { ...s, status: 'error' };
        }
        return { ...s, status: 'done' };
      }

      if (s.status === 'pending' && terminal.length > 0) {
        return { ...s, status: 'active' };
      }

      return s;
    });
  }

  private _upsertDelegate(delegate: RunDelegate): void {
    const all = dedupeDelegateList(this._allDelegates());
    const idx = all.findIndex(d => sameDelegateSlot(d, delegate));
    if (idx >= 0) {
      all[idx] = mergeDelegateRecords(all[idx], delegate);
    } else {
      all.push(delegate);
    }
    this._steps.set(this._syncStepStatusFromDelegates(
      this._reconcileDelegatesIntoSteps(this._steps(), all),
    ));
  }

  private _findDelegate(num: number): RunDelegate | undefined {
    for (const s of this._steps()) {
      const d = s.delegates.find(x => x.number === num);
      if (d) return d;
    }
    return this._unassigned().find(d => d.number === num);
  }

  private _mutateDelegate(num: number, fn: (d: RunDelegate) => RunDelegate): void {
    const all = this._allDelegates();
    const idx = all.findIndex(d => d.number === num);
    if (idx < 0) return;
    all[idx] = fn(all[idx]);
    this._steps.set(this._syncStepStatusFromDelegates(
      this._reconcileDelegatesIntoSteps(this._steps(), all),
    ));
  }
}
