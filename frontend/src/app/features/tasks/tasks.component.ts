import { Component, OnInit, OnDestroy, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute } from '@angular/router';
import { Subscription } from 'rxjs';
import { ApiService } from '../../core/services/api.service';
import { WebSocketService } from '../../core/services/websocket.service';
import { TaskBoardComponent } from './task-board/task-board.component';
import { TodoItem, TodoList, PlanSummary, PlanStepSummary } from './task.models';
import { THEME_COLORS } from '../../core/theme-colors';
import { TranslateModule } from '@ngx-translate/core';

@Component({
  selector: 'app-tasks',
  standalone: true,
  imports: [CommonModule, TaskBoardComponent, TranslateModule],
  templateUrl: './tasks.component.html',
  styleUrl: './tasks.component.scss',
})
export class TasksComponent implements OnInit, OnDestroy {
  agentId = '';
  lists = signal<TodoList[]>([]);
  items = signal<TodoItem[]>([]);
  activeListId = signal<string | null>(null);
  loading = signal(true);
  error = signal('');

  /** Plan data keyed by todo_id for cards that have linked plans */
  plansByTodoId = signal<Record<string, PlanSummary>>({});

  private wsSub?: Subscription;

  constructor(
    private route: ActivatedRoute,
    private api: ApiService,
    private ws: WebSocketService,
  ) {}

  ngOnInit(): void {
    this.agentId = this.route.snapshot.params['agentId'];
    this.loadData();
    this.subscribeWs();
  }

  ngOnDestroy(): void {
    this.wsSub?.unsubscribe();
  }

  loadData(): void {
    this.loading.set(true);
    this.error.set('');

    this.api.getTodoLists(this.agentId).subscribe({
      next: (res) => this.lists.set(res.lists || []),
      error: () => this.lists.set([]),
    });

    this.api.getTodoItems(this.agentId).subscribe({
      next: (res) => {
        const items = (res.items || []) as TodoItem[];
        this.items.set(items);
        this.loading.set(false);
        this.loadPlansForItems(items);
      },
      error: (err) => {
        this.error.set('Could not load tasks. Is the agent running?');
        this.loading.set(false);
      },
    });
  }

  selectList(listId: string | null): void {
    this.activeListId.set(listId);
  }

  /** Stable @for track when list.id is missing or duplicated in bad data. */
  listTrack(index: number, list: any): string {
    const id = list?.id;
    return id != null && id !== '' ? String(id) : `list-${index}`;
  }

  countForList(listId: string): number {
    return this.items().filter((i: any) => i.list_id === listId && i.status !== 'done').length;
  }

  get filteredItems(): any[] {
    const lid = this.activeListId();
    if (!lid) return this.items();
    return this.items().filter((i: any) => i.list_id === lid);
  }

  onStatusChange(event: { id: string; status: string }): void {
    this.api.updateTodoItem(this.agentId, event.id, { status: event.status }).subscribe({
      next: (res) => this.patchItem(res.item),
    });
  }

  onComplete(id: string): void {
    this.api.updateTodoItem(this.agentId, id, { status: 'done' }).subscribe({
      next: (res) => this.patchItem(res.item),
    });
  }

  onEdit(payload: any): void {
    const { id, ...update } = payload;
    this.api.updateTodoItem(this.agentId, id, update).subscribe({
      next: (res) => this.patchItem(res.item),
    });
  }

  onRemove(id: string): void {
    this.api.deleteTodoItem(this.agentId, id).subscribe({
      next: () => {
        this.items.update(items => items.filter((i: any) => i.id !== id));
      },
    });
  }

  onCreate(data: any): void {
    this.api.createTodoItem(this.agentId, data).subscribe({
      next: (res) => {
        if (res.item) {
          this.items.update(items => {
            if (items.some((i: any) => i.id === res.item.id)) return items;
            return [res.item, ...items];
          });
        }
      },
    });
  }

  onCreateList(): void {
    const name = window.prompt('New list name:');
    if (!name?.trim()) return;
    const color = THEME_COLORS.chart[Math.floor(Math.random() * THEME_COLORS.chart.length)];
    this.api.createTodoList(this.agentId, { name: name.trim(), color }).subscribe({
      next: (res) => {
        if (res.list) {
          this.lists.update(lists => [...lists, res.list]);
          this.activeListId.set(res.list.id);
        }
      },
    });
  }

  // ── WebSocket real-time updates ────────────────────

  private subscribeWs(): void {
    this.ws.joinAgent(this.agentId);
    this.wsSub = this.ws.onMessage(this.agentId).subscribe((msg: any) => {
      try {
        if (msg?.type === 'todo_update') {
          this.handleTodoUpdate(msg);
        } else if (msg?.type === 'plan_step_update' && msg.todo_id) {
          this.handlePlanStepUpdate(msg);
        } else if (msg?.type === 'agentic_plan' && msg.todo_id) {
          this.handleAgenticPlan(msg);
        }
      } catch (e) {
        console.error('[Tasks] ws handler', e);
      }
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
    const steps: PlanStepSummary[] = (msg.steps || []).map((s: any) => ({
      id: s.id || '',
      label: s.label || '',
      status: s.status || 'pending',
      notes: '',
    }));
    const doneCount = steps.filter(s => s.status === 'done').length;
    this.plansByTodoId.update(plans => ({
      ...plans,
      [todoId]: {
        id: msg.plan_id || '',
        title: msg.title || '',
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
          this.plansByTodoId.update(plans => ({
            ...plans,
            [todoId]: res.plan,
          }));
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
