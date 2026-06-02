import { FormsModule } from '@angular/forms';
import { Component, OnInit, computed, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { AdminApiService } from '../../core/admin-api.service';
import { PageToolbarComponent } from '../../shared/page-toolbar.component';
import { ApiErrorBannerComponent } from '../../shared/api-error-banner.component';
import { matchesSearch, paginate, sortBy, type ListQuery } from '../../shared/list.util';

@Component({
  selector: 'app-usage',
  standalone: true,
  imports: [CommonModule, RouterLink, FormsModule, PageToolbarComponent, ApiErrorBannerComponent],
  template: `
    <header class="page-header">
      <h1 class="page-title">Token usage</h1>
      <p class="page-desc">Babo Cloud inference ledger — fleet totals and per-request detail.</p>
    </header>

    <app-api-error-banner [message]="error()" [forbidden]="forbidden()" />

    @if (loading()) {
      <p class="muted">Loading…</p>
    } @else if (data()) {
      <div class="stat-grid">
        <div class="glass-card">
          <div class="stat-value">{{ data()!.totals.totalTokens | number }}</div>
          <div class="stat-label">Total tokens</div>
        </div>
        <div class="glass-card">
          <div class="stat-value">{{ data()!.totals.requestCount | number }}</div>
          <div class="stat-label">Requests</div>
        </div>
        <div class="glass-card">
          <div class="stat-value">{{ data()!.totals.promptTokens | number }}</div>
          <div class="stat-label">Prompt tokens</div>
        </div>
        <div class="glass-card">
          <div class="stat-value">{{ data()!.totals.completionTokens | number }}</div>
          <div class="stat-label">Completion tokens</div>
        </div>
      </div>

      <div class="glass-card section">
        <h2>By user</h2>
        <app-page-toolbar
          searchPlaceholder="Filter users…"
          [sortOptions]="userSortOptions"
          [search]="userQuery().search"
          [filter]="'all'"
          [sortKey]="userQuery().sortKey"
          [sortDir]="userQuery().sortDir"
          [page]="userQuery().page"
          [pageSize]="userQuery().pageSize"
          [totalItems]="data()!.byUser.length"
          [filteredItems]="filteredByUser().length"
          (queryChange)="onUserQuery($event)"
        />
        <table class="data">
          <thead>
            <tr><th>User</th><th>Requests</th><th>Total tokens</th><th></th></tr>
          </thead>
          <tbody>
            @for (u of pageByUser(); track u.userId) {
              <tr>
                <td>{{ u.email || u.userId }}</td>
                <td>{{ u.requestCount | number }}</td>
                <td>{{ u.totalTokens | number }}</td>
                <td><a [routerLink]="['/users', u.userId]">Details</a></td>
              </tr>
            }
          </tbody>
        </table>
      </div>

      <div class="glass-card section">
        <h2>Recent requests</h2>
        <app-page-toolbar
          searchPlaceholder="Filter model, user, route…"
          [sortOptions]="recentSortOptions"
          [search]="recentQuery().search"
          [filter]="'all'"
          [sortKey]="recentQuery().sortKey"
          [sortDir]="recentQuery().sortDir"
          [page]="recentQuery().page"
          [pageSize]="recentQuery().pageSize"
          [totalItems]="data()!.recent.length"
          [filteredItems]="filteredRecent().length"
          (queryChange)="onRecentQuery($event)"
        />
        <div class="limit-row">
          <label>Load depth</label>
          <select [(ngModel)]="loadLimit" (ngModelChange)="reload()">
            <option [ngValue]="50">50 rows</option>
            <option [ngValue]="100">100 rows</option>
            <option [ngValue]="200">200 rows</option>
          </select>
        </div>
        <table class="data">
          <thead>
            <tr>
              <th>When</th>
              <th>User</th>
              <th>Model</th>
              <th>Route</th>
              <th>Prompt</th>
              <th>Out</th>
              <th>Total</th>
            </tr>
          </thead>
          <tbody>
            @for (r of pageRecent(); track r.id) {
              <tr>
                <td>{{ r.createdAt | date:'short' }}</td>
                <td>
                  @if (r.userId) {
                    <a [routerLink]="['/users', r.userId]">{{ r.userEmail || r.userId }}</a>
                  } @else { — }
                </td>
                <td>{{ r.model }}</td>
                <td>{{ r.route || '—' }}</td>
                <td>{{ r.promptTokens | number }}</td>
                <td>{{ r.completionTokens | number }}</td>
                <td>{{ r.totalTokens | number }}</td>
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
    .section { margin-top: 1rem; }
    .section h2 { margin: 0 0 0.75rem; font-size: 1rem; }
    .limit-row {
      display: flex;
      align-items: center;
      gap: 0.5rem;
      margin-bottom: 0.75rem;
      font-size: 0.85rem;
    }
    .limit-row label { margin: 0; }
    .limit-row select { width: auto; }
  `],
})
export class UsageComponent implements OnInit {
  loading = signal(true);
  error = signal<string | null>(null);
  forbidden = signal(false);
  data = signal<any>(null);
  loadLimit = 100;

  userQuery = signal<ListQuery>({
    search: '',
    filter: 'all',
    sortKey: 'totalTokens',
    sortDir: 'desc',
    page: 1,
    pageSize: 15,
  });

  recentQuery = signal<ListQuery>({
    search: '',
    filter: 'all',
    sortKey: 'createdAt',
    sortDir: 'desc',
    page: 1,
    pageSize: 25,
  });

  userSortOptions = [
    { value: 'totalTokens', label: 'Tokens' },
    { value: 'requestCount', label: 'Requests' },
    { value: 'email', label: 'Email' },
  ];

  recentSortOptions = [
    { value: 'createdAt', label: 'When' },
    { value: 'totalTokens', label: 'Tokens' },
    { value: 'model', label: 'Model' },
  ];

  filteredByUser = computed(() => this.filterUserRows());
  pageByUser = computed(() => {
    const q = this.userQuery();
    return paginate(this.filteredByUser(), q.page, q.pageSize);
  });

  filteredRecent = computed(() => this.filterRecentRows());
  pageRecent = computed(() => {
    const q = this.recentQuery();
    return paginate(this.filteredRecent(), q.page, q.pageSize);
  });

  constructor(private api: AdminApiService) {}

  async ngOnInit(): Promise<void> {
    await this.reload();
  }

  async reload(): Promise<void> {
    this.loading.set(true);
    this.error.set(null);
    this.forbidden.set(false);
    try {
      this.data.set(await this.api.usage(this.loadLimit));
    } catch (e: unknown) {
      const err = e as { status?: number };
      this.forbidden.set(err?.status === 403);
      this.error.set(AdminApiService.errorMessage(e));
    } finally {
      this.loading.set(false);
    }
  }

  private filterUserRows(): any[] {
    const d = this.data();
    if (!d?.byUser) return [];
    const q = this.userQuery();
    let list = d.byUser.filter((u: any) =>
      matchesSearch(`${u.email || ''} ${u.userId}`, q.search),
    );
    return sortBy(list, q.sortKey, q.sortDir, (u, key) => {
      if (key === 'totalTokens' || key === 'requestCount') return u[key] ?? 0;
      return String(u[key] ?? '');
    });
  }

  private filterRecentRows(): any[] {
    const d = this.data();
    if (!d?.recent) return [];
    const q = this.recentQuery();
    let list = d.recent.filter((r: any) =>
      matchesSearch(`${r.userEmail || ''} ${r.model || ''} ${r.route || ''}`, q.search),
    );
    return sortBy(list, q.sortKey, q.sortDir, (r, key) => {
      if (key === 'createdAt') return new Date(r.createdAt).getTime();
      if (key === 'totalTokens') return r.totalTokens ?? 0;
      return String(r[key] ?? '');
    });
  }

  onUserQuery(ev: {
    search: string;
    filter: string;
    sortKey: string;
    sortDir: 'asc' | 'desc';
    page: number;
    pageSize: number;
  }): void {
    this.userQuery.set({ ...this.userQuery(), ...ev, filter: 'all' });
  }

  onRecentQuery(ev: {
    search: string;
    filter: string;
    sortKey: string;
    sortDir: 'asc' | 'desc';
    page: number;
    pageSize: number;
  }): void {
    this.recentQuery.set({ ...this.recentQuery(), ...ev, filter: 'all' });
  }
}
