import { Component, Input, Output, EventEmitter } from '@angular/core';
import { CommonModule } from '@angular/common';
import { CdkDragDrop, DragDropModule, moveItemInArray, transferArrayItem } from '@angular/cdk/drag-drop';
import { TaskCardComponent } from '../task-card/task-card.component';
import { TaskCreateComponent } from '../task-create/task-create.component';
import { PlanSummary } from '../task.models';

export interface BoardColumn {
  id: string;
  label: string;
  color: string;
  items: any[];
}

const COLUMNS: { id: string; label: string; color: string }[] = [
  { id: 'inbox', label: 'Inbox', color: '#94a3b8' },
  { id: 'queued', label: 'Queued', color: '#818cf8' },
  { id: 'in_progress', label: 'In Progress', color: '#f59e0b' },
  { id: 'blocked', label: 'Blocked', color: '#f87171' },
  { id: 'done', label: 'Done', color: '#34d399' },
  { id: 'deferred', label: 'Deferred', color: '#6b7280' },
];

@Component({
  selector: 'app-task-board',
  standalone: true,
  imports: [CommonModule, DragDropModule, TaskCardComponent, TaskCreateComponent],
  templateUrl: './task-board.component.html',
  styleUrl: './task-board.component.scss',
})
export class TaskBoardComponent {
  @Input() set items(value: any[]) {
    this._items = value || [];
    this.buildColumns();
  }
  @Input() lists: any[] = [];
  @Input() activeListId: string | null = null;
  @Input() plansByTodoId: Record<string, PlanSummary> = {};

  @Output() statusChange = new EventEmitter<{ id: string; status: string }>();
  @Output() completeItem = new EventEmitter<string>();
  @Output() removeItem = new EventEmitter<string>();
  @Output() createItem = new EventEmitter<any>();
  @Output() editItem = new EventEmitter<any>();

  columns: BoardColumn[] = [];
  connectedDropLists: string[] = [];
  doneCollapsed = false;
  childrenByParent: Record<string, any[]> = {};

  private _items: any[] = [];

  private buildColumns(): void {
    // Build parent→children map and filter children out of columns
    const childMap: Record<string, any[]> = {};
    const topLevel: any[] = [];
    for (const item of this._items) {
      if (item.parent_id) {
        (childMap[item.parent_id] ??= []).push(item);
      } else {
        topLevel.push(item);
      }
    }
    this.childrenByParent = childMap;

    this.columns = COLUMNS.map(col => ({
      ...col,
      items: topLevel
        .filter(i => i.status === col.id)
        .sort((a, b) => (b.updated_at || 0) - (a.updated_at || 0)),
    }));
    this.connectedDropLists = COLUMNS.map(c => 'col-' + c.id);
  }

  trackColumn(_i: number, col: BoardColumn): string {
    return col.id;
  }

  trackItem(_i: number, item: any): string {
    const id = item?.id;
    if (id != null && id !== '') return String(id);
    // Fallback per row within a column (avoid duplicate keys if id missing)
    return `row-${_i}`;
  }

  onDrop(event: CdkDragDrop<any[]>, targetStatus: string): void {
    if (event.previousContainer === event.container) {
      moveItemInArray(event.container.data, event.previousIndex, event.currentIndex);
    } else {
      const item = event.previousContainer.data[event.previousIndex];
      transferArrayItem(
        event.previousContainer.data,
        event.container.data,
        event.previousIndex,
        event.currentIndex,
      );
      this.statusChange.emit({ id: item.id, status: targetStatus });
    }
  }

  onComplete(id: string): void {
    this.completeItem.emit(id);
  }

  onRemove(id: string): void {
    this.removeItem.emit(id);
  }

  onCreate(data: any): void {
    this.createItem.emit(data);
  }

  onEdit(data: any): void {
    this.editItem.emit(data);
  }

  toggleDone(): void {
    this.doneCollapsed = !this.doneCollapsed;
  }
}
