import { Component, OnInit, computed, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { AdminApiService } from '../../core/admin-api.service';
import { PageToolbarComponent } from '../../shared/page-toolbar.component';
import { ApiErrorBannerComponent } from '../../shared/api-error-banner.component';
import { matchesSearch, paginate, sortBy, type ListQuery } from '../../shared/list.util';

@Component({
  selector: 'app-agents',
  standalone: true,
  imports: [CommonModule, PageToolbarComponent, ApiErrorBannerComponent],
  template: `
    <header class="page-header">
      <h1 class="page-title">Agents</h1>
      <p class="page-desc">All agents across customers — filter by owner, status, or runtime id.</p>
    </header>

    <app-api-error-banner [message]="error()" [forbidden]="forbidden()" />

    <app-page-toolbar
      searchPlaceholder="Search name, owner, runtime id…"
      [filterOptions]="filterOptions"
      [sortOptions]="sortOptions"
      [search]="query().search"
      [filter]="query().filter"
      [sortKey]="query().sortKey"
      [sortDir]="query().sortDir"
      [page]="query().page"
      [pageSize]="query().pageSize"
      [totalItems]="allAgents().length"
      [filteredItems]="filteredAgents().length"
      (queryChange)="onQuery($event)"
    />

    @if (loading()) {
      <p class="muted">Loading…</p>
    } @else if (!filteredAgents().length) {
      <div class="glass-card empty">No agents match your filters.</div>
    } @else {
      <div class="glass-card table-wrap">
        <table class="data">
          <thead>
            <tr>
              <th>Name</th>
              <th>Owner</th>
              <th>Runtime ID</th>
              <th>DB status</th>
              <th>Runtime</th>
              <th>Created</th>
            </tr>
          </thead>
          <tbody>
            @for (a of pageAgents(); track a.id) {
              <tr>
                <td>{{ a.name || '—' }}</td>
                <td>{{ a.user?.email || '—' }}</td>
                <td><code>{{ a.runtimeAgentId }}</code></td>
                <td>
                  <span class="badge" [class.badge-warn]="a.status !== 'active'">{{ a.status }}</span>
                </td>
                <td>{{ runtimeLabel(a) }}</td>
                <td>{{ a.createdAt | date:'mediumDate' }}</td>
              </tr>
            }
          </tbody>
        </table>
      </div>
    }
  `,
  styles: [`
    .page-header { margin-bottom: 1rem; }
    .page-title { margin: 0; font-size: 1.5rem; }
    .page-desc { margin: 0.35rem 0 0; color: var(--text-muted); font-size: 0.9rem; }
    .muted { color: var(--text-muted); }
    .empty { padding: 2rem; text-align: center; color: var(--text-muted); }
    .table-wrap { overflow-x: auto; padding: 0; }
    code { font-size: 0.72rem; }
    .badge-warn { background: rgba(229, 165, 32, 0.15); color: var(--accent-warn); }
  `],
})
export class AgentsComponent implements OnInit {
  loading = signal(true);
  error = signal<string | null>(null);
  forbidden = signal(false);
  allAgents = signal<any[]>([]);
  query = signal<ListQuery>({
    search: '',
    filter: 'all',
    sortKey: 'createdAt',
    sortDir: 'desc',
    page: 1,
    pageSize: 25,
  });

  filterOptions = [
    { value: 'all', label: 'All statuses' },
    { value: 'active', label: 'Active' },
    { value: 'inactive', label: 'Inactive' },
  ];

  sortOptions = [
    { value: 'createdAt', label: 'Created' },
    { value: 'name', label: 'Name' },
    { value: 'owner', label: 'Owner' },
    { value: 'status', label: 'DB status' },
  ];

  filteredAgents = computed(() => {
    const q = this.query();
    let list = this.allAgents().filter((a) => {
      if (q.filter !== 'all' && a.status !== q.filter) return false;
      const hay = `${a.name || ''} ${a.user?.email || ''} ${a.runtimeAgentId || ''}`;
      return matchesSearch(hay, q.search);
    });
    list = sortBy(list, q.sortKey, q.sortDir, (a, key) => {
      if (key === 'createdAt') return new Date(a.createdAt).getTime();
      if (key === 'owner') return a.user?.email || '';
      return String(a[key] ?? '');
    });
    return list;
  });

  pageAgents = computed(() => {
    const q = this.query();
    return paginate(this.filteredAgents(), q.page, q.pageSize);
  });

  constructor(private api: AdminApiService) {}

  async ngOnInit(): Promise<void> {
    this.loading.set(true);
    try {
      this.allAgents.set(await this.api.agents());
    } catch (e: unknown) {
      const err = e as { status?: number };
      this.forbidden.set(err?.status === 403);
      this.error.set(AdminApiService.errorMessage(e));
    } finally {
      this.loading.set(false);
    }
  }

  runtimeLabel(a: any): string {
    return a.runtime?.status || a.runtime?.agent_status || '—';
  }

  onQuery(ev: {
    search: string;
    filter: string;
    sortKey: string;
    sortDir: 'asc' | 'desc';
    page: number;
    pageSize: number;
  }): void {
    this.query.set({
      search: ev.search,
      filter: ev.filter,
      sortKey: ev.sortKey || 'createdAt',
      sortDir: ev.sortDir,
      page: ev.page,
      pageSize: ev.pageSize,
    });
  }
}
