import { Component, Input } from '@angular/core';
import { CommonModule } from '@angular/common';
import { CdkDragDrop, DragDropModule, transferArrayItem } from '@angular/cdk/drag-drop';
import { TaskCardComponent } from '../../tasks/task-card/task-card.component';
import { TaskCreateComponent } from '../../tasks/task-create/task-create.component';
type AnyPlanRecord = Record<string, any>;
import { ProjectService } from '../project.service';

interface BoardColumn {
  id: string;
  label: string;
  color: string;
  items: any[];
}

const COLUMNS = [
  { id: 'inbox', label: 'Inbox', color: '#94a3b8' },
  { id: 'queued', label: 'Queued', color: '#818cf8' },
  { id: 'in_progress', label: 'In Progress', color: '#f59e0b' },
  { id: 'done', label: 'Done', color: '#34d399' },
  { id: 'deferred', label: 'Deferred', color: '#6b7280' },
];

@Component({
  selector: 'app-board-panel',
  standalone: true,
  imports: [CommonModule, DragDropModule, TaskCardComponent, TaskCreateComponent],
  templateUrl: './board-panel.component.html',
  styleUrl: './board-panel.component.scss',
})
export class BoardPanelComponent {
  @Input() set items(value: any[]) {
    this._items = value || [];
    this.buildColumns();
  }
  @Input() lists: any[] = [];
  @Input() plansByTodoId: AnyPlanRecord = {};
  @Input() agentId = '';

  columns: BoardColumn[] = [];
  connectedDropLists: string[] = [];
  doneCollapsed = false;

  private _items: any[] = [];

  constructor(private svc: ProjectService) {}

  private buildColumns(): void {
    this.columns = COLUMNS.map(col => ({
      ...col,
      items: this._items
        .filter(i => i.status === col.id)
        .sort((a, b) => (b.updated_at || 0) - (a.updated_at || 0)),
    }));
    this.connectedDropLists = COLUMNS.map(c => 'bp-col-' + c.id);
  }

  trackColumn(_i: number, col: BoardColumn): string { return col.id; }
  trackItem(_i: number, item: any): string {
    return item?.id != null && item.id !== '' ? String(item.id) : `row-${_i}`;
  }

  onDrop(event: CdkDragDrop<any[]>, targetStatus: string): void {
    if (event.previousContainer !== event.container) {
      const item = event.previousContainer.data[event.previousIndex];
      transferArrayItem(
        event.previousContainer.data, event.container.data,
        event.previousIndex, event.currentIndex,
      );
      this.svc.updateItem(item.id, { status: targetStatus });
    }
  }

  onComplete(id: string): void { this.svc.completeItem(id); }
  onRemove(id: string): void { this.svc.removeItem(id); }
  onCreate(data: any): void { this.svc.createItem(data); }
  onUnblock(id: string): void { this.svc.updateItem(id, { status: 'in_progress' }); }
  onEdit(payload: any): void {
    const { id, ...update } = payload;
    this.svc.updateItem(id, update);
  }

  toggleDone(): void { this.doneCollapsed = !this.doneCollapsed; }
}
