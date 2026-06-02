import { Component, Input, Output, EventEmitter } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

@Component({
  selector: 'app-task-create',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './task-create.component.html',
  styleUrl: './task-create.component.scss',
})
export class TaskCreateComponent {
  @Input() lists: any[] = [];
  @Input() defaultListId = 'inbox';
  @Output() created = new EventEmitter<any>();

  open = false;
  title = '';
  description = '';
  priority = 'normal';
  listId = 'inbox';
  idleEligible = false;

  toggle(): void {
    this.open = !this.open;
    if (this.open) {
      this.listId = this.defaultListId;
    }
  }

  submit(): void {
    const trimmed = this.title.trim();
    if (!trimmed) return;

    this.created.emit({
      title: trimmed,
      description: this.description.trim(),
      priority: this.priority,
      list_id: this.listId,
      idle_eligible: this.idleEligible,
      source: 'user',
    });

    this.title = '';
    this.description = '';
    this.priority = 'normal';
    this.idleEligible = false;
  }

  onKeydown(event: KeyboardEvent): void {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      this.submit();
    }
    if (event.key === 'Escape') {
      this.open = false;
    }
  }
}
