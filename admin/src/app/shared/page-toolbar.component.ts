import { Component, EventEmitter, Input, Output } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

export interface ToolbarFilterOption {
  value: string;
  label: string;
}

export interface ToolbarSortOption {
  value: string;
  label: string;
}

@Component({
  selector: 'app-page-toolbar',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="toolbar glass-panel">
      <div class="toolbar-row">
        <input
          class="search"
          type="search"
          [placeholder]="searchPlaceholder"
          [(ngModel)]="search"
          (ngModelChange)="emitChange()"
        />
        @if (filterOptions.length) {
          <select [(ngModel)]="filter" (ngModelChange)="emitChange()">
            @for (o of filterOptions; track o.value) {
              <option [value]="o.value">{{ o.label }}</option>
            }
          </select>
        }
        @if (sortOptions.length) {
          <select [(ngModel)]="sortKey" (ngModelChange)="emitChange()">
            @for (o of sortOptions; track o.value) {
              <option [value]="o.value">{{ o.label }}</option>
            }
          </select>
          <button type="button" class="btn btn-ghost sort-dir" (click)="toggleSortDir()">
            {{ sortDir === 'asc' ? '↑' : '↓' }}
          </button>
        }
        <select class="page-size" [(ngModel)]="pageSize" (ngModelChange)="onPageSizeChange()">
          @for (n of pageSizes; track n) {
            <option [value]="n">{{ n }} / page</option>
          }
        </select>
      </div>
      <div class="toolbar-meta">
        <span class="count">{{ summary }}</span>
        <div class="pager">
          <button type="button" class="btn btn-ghost" [disabled]="page <= 1" (click)="setPage(page - 1)">Prev</button>
          <span>{{ page }} / {{ totalPages }}</span>
          <button type="button" class="btn btn-ghost" [disabled]="page >= totalPages" (click)="setPage(page + 1)">Next</button>
        </div>
      </div>
    </div>
  `,
  styles: [`
    .toolbar {
      padding: 1rem 1.25rem;
      margin-bottom: 1rem;
      display: flex;
      flex-direction: column;
      gap: 0.75rem;
    }
    .toolbar-row {
      display: flex;
      flex-wrap: wrap;
      gap: 0.5rem;
      align-items: center;
    }
    .search { flex: 1; min-width: 200px; }
    select { width: auto; min-width: 140px; }
    .page-size { min-width: 110px; }
    .sort-dir { min-width: 2.5rem; padding: 0.5rem; }
    .toolbar-meta {
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 0.5rem;
      font-size: 0.85rem;
      color: var(--text-muted);
    }
    .pager { display: flex; align-items: center; gap: 0.5rem; }
    .pager .btn { padding: 0.35rem 0.65rem; font-size: 0.8rem; }
  `],
})
export class PageToolbarComponent {
  @Input() searchPlaceholder = 'Search…';
  @Input() filterOptions: ToolbarFilterOption[] = [];
  @Input() sortOptions: ToolbarSortOption[] = [];
  @Input() pageSizes = [10, 25, 50, 100];
  @Input() search = '';
  @Input() filter = 'all';
  @Input() sortKey = '';
  @Input() sortDir: 'asc' | 'desc' = 'desc';
  @Input() page = 1;
  @Input() pageSize = 25;
  @Input() totalItems = 0;
  @Input() filteredItems = 0;

  @Output() queryChange = new EventEmitter<{
    search: string;
    filter: string;
    sortKey: string;
    sortDir: 'asc' | 'desc';
    page: number;
    pageSize: number;
  }>();

  get totalPages(): number {
    return Math.max(1, Math.ceil(this.filteredItems / Math.max(1, this.pageSize)));
  }

  get summary(): string {
    const shown = Math.min(this.pageSize, Math.max(0, this.filteredItems - (this.page - 1) * this.pageSize));
    if (this.filteredItems === 0) return 'No results';
    return `Showing ${shown} of ${this.filteredItems} (total ${this.totalItems})`;
  }

  emitChange(): void {
    this.queryChange.emit({
      search: this.search,
      filter: this.filter,
      sortKey: this.sortKey,
      sortDir: this.sortDir,
      page: this.page,
      pageSize: this.pageSize,
    });
  }

  toggleSortDir(): void {
    this.sortDir = this.sortDir === 'asc' ? 'desc' : 'asc';
    this.emitChange();
  }

  onPageSizeChange(): void {
    this.page = 1;
    this.emitChange();
  }

  setPage(p: number): void {
    this.page = Math.min(Math.max(1, p), this.totalPages);
    this.emitChange();
  }
}
