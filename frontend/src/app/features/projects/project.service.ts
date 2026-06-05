import { Injectable, signal, computed, inject } from '@angular/core';
import { Subscription } from 'rxjs';
import { ApiService } from '../../core/services/api.service';
import { WebSocketService } from '../../core/services/websocket.service';
import { RunViewService } from '../../core/services/run-view.service';
import { Team, TodoItem, TodoList, PlanSummary, Timeline } from './project.models';

@Injectable({ providedIn: 'root' })
export class ProjectService {
  teams = signal<Team[]>([]);
  items = signal<TodoItem[]>([]);
  lists = signal<TodoList[]>([]);
  plansByTodoId = signal<Record<string, PlanSummary>>({});
  timeline = signal<Timeline | null>(null);
  loading = signal(true);
  error = signal('');

  activeTeams = computed(() =>
    this.teams().filter(t => !['completed', 'failed'].includes(t.status))
  );

  /** Teams created by advance but never launched (orchestrator stalled). */
  unlaunchedTeams = computed(() =>
    this.teams().filter(t => t.status === 'created'),
  );

  /** True once any REST payload arrived — used to avoid a full-page spinner. */
  hasAnyData = computed(
    () =>
      this.teams().length > 0
      || this.items().length > 0
      || this.lists().length > 0,
  );

  private static readonly LOAD_TIMEOUT_MS = 10_000;

  private agentId = '';
  private wsSub?: Subscription;
  private loadGeneration = 0;
  private loadTimeout?: ReturnType<typeof setTimeout>;

  private readonly runView = inject(RunViewService);

  constructor(
    private api: ApiService,
    private ws: WebSocketService,
  ) {}

  init(agentId: string): void {
    this.agentId = agentId;
    this.runView.bindAgent(agentId);
    this.loadData();
    // Let REST responses start before replaying buffered WS events.
    queueMicrotask(() => this.subscribeWs());
  }

  destroy(): void {
    this.loadGeneration++;
    this.clearLoadTimeout();
    this.wsSub?.unsubscribe();
  }

  loadData(): void {
    const gen = ++this.loadGeneration;
    this.clearLoadTimeout();
    this.loading.set(true);
    this.error.set('');

    let pending = 3;
    let itemsLoaded = false;
    let anySuccess = false;

    const finishLoad = (opts?: { error?: boolean }) => {
      if (gen !== this.loadGeneration) return;
      pending = Math.max(0, pending - 1);
      if (opts?.error && !anySuccess && pending === 0) {
        this.error.set('Could not load project data. Is the agent running?');
      }
      if (pending === 0 || anySuccess) {
        this.loading.set(false);
        this.clearLoadTimeout();
      }
    };

    this.loadTimeout = setTimeout(() => {
      if (gen !== this.loadGeneration) return;
      this.loading.set(false);
      if (!anySuccess && !this.hasAnyData()) {
        this.error.set('Could not load project data. Is the agent running?');
      }
    }, ProjectService.LOAD_TIMEOUT_MS);

    this.api.getTeams(this.agentId, true).subscribe({
      next: (res) => {
        if (gen !== this.loadGeneration) return;
        anySuccess = true;
        const teams = res.teams || [];
        this.teams.set(teams);
        this.runView.hydrateTeams(teams);
        this.autoLoadTimeline(teams);
        this.loading.set(false);
      },
      error: () => {
        if (gen !== this.loadGeneration) return;
        this.teams.set([]);
        finishLoad({ error: true });
      },
      complete: () => finishLoad(),
    });

    this.api.getAgentDelegates(this.agentId).subscribe({
      next: (data) => {
        if (gen !== this.loadGeneration) return;
        this.runView.hydrateDelegates(data);
      },
      error: () => {},
    });

    this.api.getTodoLists(this.agentId).subscribe({
      next: (res) => {
        if (gen !== this.loadGeneration) return;
        anySuccess = true;
        this.lists.set(res.lists || []);
        this.loading.set(false);
      },
      error: () => {
        if (gen !== this.loadGeneration) return;
        this.lists.set([]);
        finishLoad({ error: true });
      },
      complete: () => finishLoad(),
    });

    this.api.getTodoItems(this.agentId).subscribe({
      next: (res) => {
        if (gen !== this.loadGeneration) return;
        anySuccess = true;
        itemsLoaded = true;
        const items = (res.items || []) as TodoItem[];
        this.items.set(items);
        this.loading.set(false);
        this.loadPlansForItems(items);
        if (!this.timeline()) {
          this.autoLoadTimeline(this.teams());
        }
      },
      error: () => {
        if (gen !== this.loadGeneration) return;
        finishLoad({ error: true });
      },
      complete: () => {
        if (gen === this.loadGeneration && !itemsLoaded && anySuccess) {
          // Teams/lists loaded; todo items failed — still show the page.
          this.loading.set(false);
        }
        finishLoad({ error: !itemsLoaded && !anySuccess });
      },
    });
  }

  private clearLoadTimeout(): void {
    if (this.loadTimeout) {
      clearTimeout(this.loadTimeout);
      this.loadTimeout = undefined;
    }
  }

  refreshTeams(): void {
    this.api.getTeams(this.agentId, true).subscribe({
      next: (res) => this.teams.set(res.teams || []),
    });
  }

  loadTimeline(planId: string): void {
    this.api.getTimeline(this.agentId, planId).subscribe({
      next: (tl) => {
        this.timeline.set(tl);
        if (tl?.waves?.length) {
          const steps = tl.waves.flatMap((w: { steps?: PlanSummary['steps'] }) => w.steps || []);
          if (steps.length) {
            this.runView.hydratePlan({
              id: tl.plan_id,
              title: tl.title || '',
              status: tl.status || 'in_progress',
              progress: '',
              steps,
            });
          }
        }
      },
      error: () => this.timeline.set(null),
    });
  }

  // ── Team actions ───────────────────────────────────────────

  pauseTeam(teamId: string): void {
    this.api.pauseTeam(this.agentId, teamId).subscribe({
      next: () => this.refreshTeams(),
    });
  }

  resumeTeam(teamId: string): void {
    this.api.resumeTeam(this.agentId, teamId).subscribe({
      next: () => this.refreshTeams(),
    });
  }

  disbandTeam(teamId: string): void {
    this.api.disbandTeam(this.agentId, teamId).subscribe({
      next: () => this.refreshTeams(),
    });
  }

  advanceTeam(teamId: string): void {
    this.api.advanceTeam(this.agentId, teamId).subscribe({
      next: () => this.refreshTeams(),
    });
  }

  hintMember(teamId: string, memberIdx: number, message: string): void {
    this.api.hintTeamMember(this.agentId, teamId, memberIdx, message).subscribe();
  }

  skipWave(teamId: string): void {
    this.api.skipWave(this.agentId, teamId).subscribe({
      next: () => this.refreshTeams(),
    });
  }

  forceStartWave(planId: string, waveIndex: number): void {
    this.api.forceStartWave(this.agentId, planId, waveIndex).subscribe({
      next: () => {
        this.refreshTeams();
        this.loadTimeline(planId);
      },
    });
  }

  // ── Todo actions ───────────────────────────────────────────

  updateItem(itemId: string, update: any): void {
    this.api.updateTodoItem(this.agentId, itemId, update).subscribe({
      next: (res) => this.patchItem(res.item),
    });
  }

  completeItem(itemId: string): void {
    this.updateItem(itemId, { status: 'done' });
  }

  removeItem(itemId: string): void {
    this.api.deleteTodoItem(this.agentId, itemId).subscribe({
      next: () => this.items.update(items => items.filter(i => i.id !== itemId)),
    });
  }

  createItem(data: any): void {
    this.api.createTodoItem(this.agentId, data).subscribe({
      next: (res) => {
        if (res.item) {
          this.items.update(items => {
            if (items.some(i => i.id === res.item.id)) return items;
            return [res.item, ...items];
          });
        }
      },
    });
  }

  createList(name: string, color: string): void {
    this.api.createTodoList(this.agentId, { name, color }).subscribe({
      next: (res) => {
        if (res.list) {
          this.lists.update(lists => [...lists, res.list]);
        }
      },
    });
  }

  // ── Timeline auto-detection ────────────────────────────────

  private autoLoadTimeline(teams: Team[]): void {
    // Find the most recent team with a plan_id
    const withPlan = teams
      .filter(t => t.plan_id)
      .sort((a, b) => b.created_at - a.created_at);

    if (withPlan.length > 0) {
      this.loadTimeline(withPlan[0].plan_id);
      return;
    }

    // Fallback: look at items with plan_id
    const items = this.items();
    const planItems = items.filter(i => i.plan_id).sort((a, b) => b.updated_at - a.updated_at);
    if (planItems.length > 0) {
      this.loadTimeline(planItems[0].plan_id);
    }
  }

  // ── Command bar ────────────────────────────────────────────

  sendCommand(message: string, context: any = {}): void {
    this.api.sendCommand(this.agentId, message, {
      view: 'projects',
      ...context,
    }).subscribe();
  }

  // ── WebSocket ──────────────────────────────────────────────

  private subscribeWs(): void {
    this.ws.joinAgent(this.agentId);
    this.wsSub = this.ws.onMessage(this.agentId).subscribe((msg: any) => {
      try {
        this.handleWsMessage(msg);
      } catch (e) {
        console.error('[Projects] ws handler', e);
      }
    });
  }

  private handleWsMessage(msg: any): void {
    if (!msg?.type) return;
    this.runView.handleMessage(msg);

    switch (msg.type) {
      case 'team_created':
      case 'team_launched':
      case 'team_advanced':
      case 'team_paused':
      case 'team_resumed':
      case 'team_disbanded':
        this.handleTeamEvent(msg);
        break;
      case 'team_member_complete':
      case 'team_member_progress':
      case 'team_member_spawned':
        this.handleTeamMemberEvent(msg);
        break;
      case 'team_complete':
        this.handleTeamEvent(msg);
        break;
      case 'todo_update':
        this.handleTodoUpdate(msg);
        break;
      case 'plan_step_update':
        if (msg.todo_id) {
          this.handlePlanStepUpdate(msg);
          this.loadPlanForTodo(String(msg.todo_id));
        }
        break;
      case 'agentic_plan':
        if (msg.todo_id) this.handleAgenticPlan(msg);
        break;
    }
  }

  private handleTeamEvent(msg: any): void {
    if (!msg.team) return;
    const incoming = msg.team as Team;
    this.teams.update(teams => {
      const idx = teams.findIndex(t => t.id === incoming.id);
      if (idx >= 0) {
        const copy = [...teams];
        copy[idx] = incoming;
        return copy;
      }
      return [incoming, ...teams];
    });
    if (['team_created', 'team_advanced', 'team_complete'].includes(msg.type) && incoming.plan_id) {
      this.loadTimeline(incoming.plan_id);
    }
  }

  private handleTeamMemberEvent(msg: any): void {
    if (!msg.team) return;
    const incoming = msg.team as Team;
    this.teams.update(teams => {
      const idx = teams.findIndex(t => t.id === incoming.id);
      if (idx >= 0) {
        const copy = [...teams];
        copy[idx] = incoming;
        return copy;
      }
      return teams;
    });
  }

  private handleTodoUpdate(msg: any): void {
    const item = msg.item;
    if (!item || item.id == null) return;

    switch (msg.action) {
      case 'added':
        this.items.update(items => {
          if (items.some(i => i.id === item.id)) return items;
          return [item, ...items];
        });
        if (item.plan_id) this.loadPlanForTodo(item.id);
        break;
      case 'updated':
      case 'completed':
        this.patchItem(item);
        if (item.plan_id) this.loadPlanForTodo(item.id);
        break;
      case 'removed':
        this.items.update(items => items.filter(i => i.id !== item.id));
        break;
    }
  }

  private handlePlanStepUpdate(msg: any): void {
    const todoId = msg.todo_id;
    const stepIndex = msg.step_index ?? -1;
    const newStatus = msg.status || 'done';
    if (stepIndex < 0) return;

    this.plansByTodoId.update(plans => {
      const plan = plans[todoId];
      if (!plan || stepIndex >= plan.steps.length) return plans;
      const updated = { ...plans };
      const updatedSteps = [...plan.steps];
      updatedSteps[stepIndex] = { ...updatedSteps[stepIndex], status: newStatus };
      const doneCount = updatedSteps.filter(s => s.status === 'done').length;
      updated[todoId] = {
        ...plan,
        steps: updatedSteps,
        progress: `${doneCount}/${updatedSteps.length} steps done`,
      };
      return updated;
    });
  }

  private handleAgenticPlan(msg: any): void {
    const todoId = msg.todo_id;
    const steps = (msg.steps || []).map((s: any) => ({
      id: s.id || '', label: s.label || '',
      status: s.status || 'pending', notes: '',
    }));
    const doneCount = steps.filter((s: any) => s.status === 'done').length;
    this.plansByTodoId.update(plans => ({
      ...plans,
      [todoId]: {
        id: msg.plan_id || '', title: msg.title || '',
        status: 'in_progress',
        progress: `${doneCount}/${steps.length} steps done`,
        steps,
      },
    }));
  }

  private loadPlansForItems(items: TodoItem[]): void {
    for (const item of items) {
      if (item.plan_id && item.status !== 'done') {
        this.loadPlanForTodo(item.id);
      }
    }
  }

  private loadPlanForTodo(todoId: string): void {
    this.api.getTodoPlan(this.agentId, todoId).subscribe({
      next: (res: any) => {
        if (res.plan) {
          this.plansByTodoId.update(plans => ({ ...plans, [todoId]: res.plan }));
          this.runView.hydratePlan(res.plan, todoId);
        }
      },
    });
  }

  private patchItem(updated: any): void {
    if (!updated) return;
    this.items.update(items => {
      const idx = items.findIndex(i => i.id === updated.id);
      if (idx >= 0) {
        const copy = [...items];
        copy[idx] = updated;
        return copy;
      }
      return [updated, ...items];
    });
  }
}
