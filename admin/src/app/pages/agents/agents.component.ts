import { Component, OnInit, computed, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { AdminApiService } from '../../core/admin-api.service';
import { PageToolbarComponent } from '../../shared/page-toolbar.component';
import { ApiErrorBannerComponent } from '../../shared/api-error-banner.component';
import { matchesSearch, paginate, sortBy, type ListQuery } from '../../shared/list.util';
import { formatNumber, runtimeStatusLabel, statusClass } from '../../shared/format.util';

@Component({
  selector: 'app-agents',
  standalone: true,
  imports: [CommonModule, RouterLink, PageToolbarComponent, ApiErrorBannerComponent],
  template: `
    <header class="page-header">
      <h1 class="page-title">Agents</h1>
      <p class="page-desc">Fleet registry — open operations for live stats, cleanup, sleep, and eviction.</p>
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
              <th>Live status</th>
              <th>Turns</th>
              <th>DB</th>
              <th>Created</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            @for (a of pageAgents(); track a.id) {
              <tr>
                <td>
                  <a [routerLink]="['/agents', a.id]">{{ a.name || '—' }}</a>
                </td>
                <td>{{ a.user?.email || '—' }}</td>
                <td>
                  <span class="status-pill sm" [class]="statusClass(runtimeStatusLabel({ live: a.runtime, runtime: a.runtime }))">
                    {{ runtimeStatusLabel({ live: a.runtime, runtime: a.runtime }) }}
                  </span>
                </td>
                <td>{{ formatNumber(a.runtime?.turn_count) }}</td>
                <td><span class="badge" [class.badge-warn]="a.status !== 'active'">{{ a.status }}</span></td>
                <td>{{ a.createdAt | date:'mediumDate' }}</td>
                <td class="actions">
                  <a [routerLink]="['/agents', a.id]">Ops</a>
                  <button type="button" class="link-btn danger" (click)="remove(a)">Delete</button>
                </td>
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
    .actions { white-space: nowrap; }
    .actions a, .link-btn { margin-right: 0.65rem; font-size: 0.85rem; }
    .link-btn {
      background: none;
      border: none;
      color: var(--accent-primary);
      cursor: pointer;
      padding: 0;
      font-weight: 600;
    }
    .link-btn.danger { color: var(--accent-danger); }
    .badge-warn { background: rgba(229, 165, 32, 0.15); color: var(--accent-warn); }
    .status-pill {
      display: inline-block;
      padding: 0.15rem 0.5rem;
      border-radius: 999px;
      font-size: 0.68rem;
      font-weight: 600;
      text-transform: capitalize;
    }
    .status-pill.ok { background: rgba(20, 184, 166, 0.15); color: var(--accent-success); }
    .status-pill.busy { background: rgba(124, 91, 245, 0.15); color: var(--accent-primary); }
    .status-pill.sleep { background: rgba(229, 165, 32, 0.15); color: var(--accent-warn); }
    .status-pill.bad { background: rgba(192, 57, 43, 0.12); color: var(--accent-danger); }
    .status-pill.neutral { background: rgba(0,0,0,0.06); color: var(--text-secondary); }
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

  formatNumber = formatNumber;
  runtimeStatusLabel = runtimeStatusLabel;
  statusClass = statusClass;

  filterOptions = [
    { value: 'all', label: 'All DB statuses' },
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

  async remove(a: { id: string; name?: string }): Promise<void> {
    if (!confirm(`Delete agent "${a.name || a.id}" from database and runtime?`)) return;
    try {
      await this.api.deleteAgentDb(a.id);
      this.allAgents.update((list) => list.filter((x) => x.id !== a.id));
    } catch (e: unknown) {
      alert(AdminApiService.errorMessage(e));
    }
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
