import { Component, OnInit, computed, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { AdminApiService } from '../../core/admin-api.service';
import { PageToolbarComponent } from '../../shared/page-toolbar.component';
import { ApiErrorBannerComponent } from '../../shared/api-error-banner.component';
import { matchesSearch, paginate, sortBy, type ListQuery } from '../../shared/list.util';
import { subscriptionStatusLabel } from '../../shared/format.util';

@Component({
  selector: 'app-users',
  standalone: true,
  imports: [CommonModule, RouterLink, PageToolbarComponent, ApiErrorBannerComponent],
  template: `
    <header class="page-header">
      <h1 class="page-title">Users</h1>
      <p class="page-desc">Manage accounts, roles, and open per-user token usage.</p>
    </header>

    <app-api-error-banner [message]="error()" [forbidden]="forbidden()" />

    <app-page-toolbar
      searchPlaceholder="Search email or name…"
      [filterOptions]="filterOptions"
      [sortOptions]="sortOptions"
      [search]="query().search"
      [filter]="query().filter"
      [sortKey]="query().sortKey"
      [sortDir]="query().sortDir"
      [page]="query().page"
      [pageSize]="query().pageSize"
      [totalItems]="allUsers().length"
      [filteredItems]="filteredUsers().length"
      (queryChange)="onQuery($event)"
    />

    @if (loading()) {
      <p class="muted">Loading…</p>
    } @else if (!filteredUsers().length) {
      <div class="glass-card empty">No users match your filters.</div>
    } @else {
      <div class="glass-card table-wrap">
        <table class="data">
          <thead>
            <tr>
              <th>Email</th>
              <th>Name</th>
              <th>Role</th>
              @if (billingEnabled()) {
                <th>Cloud</th>
              }
              <th>Agents</th>
              <th>API keys</th>
              <th>Created</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            @for (u of pageUsers(); track u.id) {
              <tr>
                <td>{{ u.email }}</td>
                <td>{{ u.displayName || '—' }}</td>
                <td>
                  <span class="badge" [class.badge-admin]="u.role === 'admin'" [class.badge-user]="u.role !== 'admin'">
                    {{ u.role }}
                  </span>
                </td>
                @if (billingEnabled()) {
                  <td>
                    @if (u.subscription) {
                      <span class="badge sm" [class.badge-admin]="u.subscription.status === 'lifetime_comp'">
                        {{ subscriptionStatusLabel(u.subscription.status) }}
                      </span>
                    } @else {
                      <span class="muted">—</span>
                    }
                  </td>
                }
                <td>{{ u.agentCount }}</td>
                <td>{{ u.apiKeyCount }}</td>
                <td>{{ u.createdAt | date:'mediumDate' }}</td>
                <td class="actions">
                  <a [routerLink]="['/users', u.id]">Details</a>
                  <button type="button" class="link-btn" (click)="toggleRole(u)">
                    {{ u.role === 'admin' ? 'Demote' : 'Promote' }}
                  </button>
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
    table.data { min-width: 720px; }
    .actions { white-space: nowrap; }
    .actions a, .link-btn { margin-right: 0.75rem; font-size: 0.85rem; }
    .link-btn {
      background: none;
      border: none;
      color: var(--accent-primary);
      cursor: pointer;
      padding: 0;
      font-weight: 600;
    }
    .badge.sm { font-size: 0.68rem; }
  `],
})
export class UsersComponent implements OnInit {
  loading = signal(true);
  error = signal<string | null>(null);
  forbidden = signal(false);
  allUsers = signal<any[]>([]);
  query = signal<ListQuery>({
    search: '',
    filter: 'all',
    sortKey: 'createdAt',
    sortDir: 'desc',
    page: 1,
    pageSize: 25,
  });

  filterOptions = [
    { value: 'all', label: 'All roles' },
    { value: 'admin', label: 'Administrators' },
    { value: 'user', label: 'Users only' },
  ];

  billingEnabled = signal(false);

  sortOptions = [
    { value: 'createdAt', label: 'Created' },
    { value: 'email', label: 'Email' },
    { value: 'agentCount', label: 'Agents' },
    { value: 'apiKeyCount', label: 'API keys' },
  ];

  filteredUsers = computed(() => {
    const q = this.query();
    let list = this.allUsers().filter((u) => {
      if (q.filter !== 'all' && u.role !== q.filter) return false;
      const hay = `${u.email} ${u.displayName || ''}`;
      return matchesSearch(hay, q.search);
    });
    list = sortBy(list, q.sortKey, q.sortDir, (u, key) => {
      if (key === 'createdAt') return new Date(u.createdAt).getTime();
      if (key === 'agentCount' || key === 'apiKeyCount') return u[key] ?? 0;
      return String(u[key] ?? '');
    });
    return list;
  });

  pageUsers = computed(() => {
    const q = this.query();
    return paginate(this.filteredUsers(), q.page, q.pageSize);
  });

  constructor(private api: AdminApiService) {}

  subscriptionStatusLabel = subscriptionStatusLabel;

  async ngOnInit(): Promise<void> {
    try {
      const platform = await this.api.platform();
      this.billingEnabled.set(!!platform.billingEnabled);
    } catch { /* ignore */ }
    await this.reload();
  }

  async reload(): Promise<void> {
    this.loading.set(true);
    this.error.set(null);
    this.forbidden.set(false);
    try {
      this.allUsers.set(await this.api.users());
    } catch (e: unknown) {
      const err = e as { status?: number };
      this.forbidden.set(err?.status === 403);
      this.error.set(AdminApiService.errorMessage(e));
    } finally {
      this.loading.set(false);
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

  async toggleRole(u: { id: string; role: string }): Promise<void> {
    const next = u.role === 'admin' ? 'user' : 'admin';
    if (!confirm(`${next === 'admin' ? 'Promote' : 'Demote'} ${u.id}?`)) return;
    try {
      await this.api.updateRole(u.id, next);
      this.allUsers.update((list) =>
        list.map((row) => (row.id === u.id ? { ...row, role: next } : row)),
      );
    } catch (e: unknown) {
      alert(AdminApiService.errorMessage(e));
    }
  }
}
