import { Component, Input, Output, EventEmitter, ViewChild, ElementRef, ChangeDetectorRef, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { TranslateModule, TranslateService } from '@ngx-translate/core';
import { PlanSummary } from '../task.models';

@Component({
  selector: 'app-task-card',
  standalone: true,
  imports: [CommonModule, FormsModule, TranslateModule],
  templateUrl: './task-card.component.html',
  styleUrl: './task-card.component.scss',
})
export class TaskCardComponent {
  private readonly translate = inject(TranslateService);

  @Input() task: any = {};
  @Input() lists: any[] = [];
  @Input() plan: PlanSummary | null = null;
  @Input() children: any[] = [];
  @Output() complete = new EventEmitter<string>();
  @Output() remove = new EventEmitter<string>();
  @Output() edit = new EventEmitter<any>();

  expanded = false;
  editMode = false;

  @ViewChild('editTitleInput') editTitleInputRef?: ElementRef<HTMLInputElement>;

  // Edit form state
  editTitle = '';
  editDescription = '';
  editPriority = 'normal';
  editIdleEligible = false;
  editDueDate = '';

  get priorityClass(): string {
    return `priority-${this.task.priority || 'normal'}`;
  }

  get sourceIcon(): string {
    switch (this.task.source) {
      case 'agent':
        return '\u2699';
      case 'channel':
        return '\u2709';
      default:
        return '\u263A';
    }
  }

  get listName(): string {
    const list = this.lists.find((l: any) => l.id === this.task.list_id);
    return list?.name || this.task.list_id || '';
  }

  get listColor(): string {
    const list = this.lists.find((l: any) => l.id === this.task.list_id);
    return list?.color || '#94a3b8';
  }

  get dueDateLabel(): string {
    if (!this.task.due_date) return '';
    try {
      const due = new Date(this.task.due_date);
      const now = new Date();
      const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate());
      const dueStart = new Date(due.getFullYear(), due.getMonth(), due.getDate());
      this._dueDiffDays = Math.round((dueStart.getTime() - todayStart.getTime()) / 86400000);
      if (this._dueDiffDays < 0) {
        return this.t('tasks.due.overdue', { days: Math.abs(this._dueDiffDays) });
      }
      if (this._dueDiffDays === 0) return this.t('tasks.due.today');
      if (this._dueDiffDays === 1) return this.t('tasks.due.tomorrow');
      return this.t('tasks.due.inDays', { days: this._dueDiffDays });
    } catch {
      return '';
    }
  }

  // Cached by dueDateLabel getter — consistent, avoids double Date parsing.
  private _dueDiffDays = 0;

  get dueDateOverdue(): boolean {
    return !!this.task.due_date && this._dueDiffDays < 0;
  }

  get planDoneCount(): number {
    return this.plan?.steps.filter(s => s.status === 'done').length ?? 0;
  }

  get planTotalCount(): number {
    return this.plan?.steps.length ?? 0;
  }

  get planProgressPercent(): number {
    if (!this.planTotalCount) return 0;
    return Math.round((this.planDoneCount / this.planTotalCount) * 100);
  }

  get childDoneCount(): number {
    return this.children.filter(c => c.status === 'done').length;
  }

  get childProgressPercent(): number {
    if (!this.children.length) return 0;
    return Math.round((this.childDoneCount / this.children.length) * 100);
  }

  childStatusIcon(status: string): string {
    switch (status) {
      case 'done': return '\u2713';
      case 'in_progress': return '\u25B6';
      case 'blocked':
      case 'failed': return '\u2717';
      case 'queued': return '\u25CB';
      default: return '\u25CB';
    }
  }

  toggleExpand(): void {
    if (!this.editMode) {
      this.expanded = !this.expanded;
    }
  }

  constructor(private cdr: ChangeDetectorRef) {}

  private t(key: string, params?: Record<string, unknown>): string {
    return this.translate.instant(key, params);
  }

  startEdit(event: Event): void {
    event.stopPropagation();
    this.editTitle = this.task.title || '';
    this.editDescription = this.task.description || '';
    this.editPriority = this.task.priority || 'normal';
    this.editIdleEligible = this.task.idle_eligible || false;
    this.editDueDate = this.task.due_date || '';
    this.editMode = true;
    this.expanded = true;
    // Focus the title input after Angular renders the edit form into the DOM
    this.cdr.detectChanges();
    this.editTitleInputRef?.nativeElement.focus();
  }

  saveEdit(event: Event): void {
    event.stopPropagation();
    const trimmed = this.editTitle.trim();
    if (!trimmed) return;
    this.edit.emit({
      id: this.task.id,
      title: trimmed,
      description: this.editDescription.trim(),
      priority: this.editPriority,
      idle_eligible: this.editIdleEligible,
      due_date: this.editDueDate || null,
    });
    this.editMode = false;
  }

  cancelEdit(event: Event): void {
    event.stopPropagation();
    this.editMode = false;
  }

  onComplete(event: Event): void {
    event.stopPropagation();
    this.complete.emit(this.task.id);
  }

  onRemove(event: Event): void {
    event.stopPropagation();
    this.remove.emit(this.task.id);
  }

  formatTime(ts: number): string {
    if (!ts) return '';
    const d = new Date(ts * 1000);
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    if (diffMins < 1) return this.t('tasks.time.justNow');
    if (diffMins < 60) return this.t('tasks.time.minutesAgo', { count: diffMins });
    const diffHrs = Math.floor(diffMins / 60);
    if (diffHrs < 24) return this.t('tasks.time.hoursAgo', { count: diffHrs });
    const diffDays = Math.floor(diffHrs / 24);
    return this.t('tasks.time.daysAgo', { count: diffDays });
  }
}
