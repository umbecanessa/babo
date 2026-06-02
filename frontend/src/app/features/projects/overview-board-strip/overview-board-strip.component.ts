import { Component, EventEmitter, Input, Output } from '@angular/core';
import { CommonModule } from '@angular/common';
import { TodoItem, PlanSummary } from '../project.models';

@Component({
  selector: 'app-overview-board-strip',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './overview-board-strip.component.html',
  styleUrl: './overview-board-strip.component.scss',
})
export class OverviewBoardStripComponent {
  @Input() items: TodoItem[] = [];
  @Input() plansByTodoId: Record<string, PlanSummary> = {};

  @Output() openBoard = new EventEmitter<void>();

  get inProgress(): TodoItem[] {
    return this.items
      .filter((i) => i.status === 'in_progress')
      .slice(0, 6);
  }

  get blocked(): TodoItem[] {
    return this.items
      .filter((i) => i.status === 'queued' && (i.depends_on?.length ?? 0) > 0)
      .slice(0, 6);
  }

  get hasCards(): boolean {
    return this.inProgress.length > 0 || this.blocked.length > 0;
  }

  planProgress(item: TodoItem): string | null {
    const plan = this.plansByTodoId[item.id];
    if (!plan?.steps?.length) return null;
    const done = plan.steps.filter((s) => s.status === 'done').length;
    return `${done}/${plan.steps.length}`;
  }
}
