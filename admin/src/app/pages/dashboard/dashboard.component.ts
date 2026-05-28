import { Component, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { AdminApiService } from '../../core/admin-api.service';
import { ApiErrorBannerComponent } from '../../shared/api-error-banner.component';

@Component({
  selector: 'app-dashboard',
  standalone: true,
  imports: [CommonModule, RouterLink, ApiErrorBannerComponent],
  template: `
    <header class="page-header">
      <h1 class="page-title">Dashboard</h1>
      <p class="page-desc">Fleet overview for Babo Cloud operations.</p>
    </header>

    <app-api-error-banner [message]="error()" [forbidden]="forbidden()" />

    @if (loading()) {
      <p class="muted">Loading…</p>
    } @else if (stats()) {
      <div class="stat-grid">
        <div class="glass-card stat-card">
          <div class="stat-value">{{ stats()!.users }}</div>
          <div class="stat-label">Users</div>
          <a routerLink="/users">Manage →</a>
        </div>
        <div class="glass-card stat-card">
          <div class="stat-value">{{ stats()!.agents }}</div>
          <div class="stat-label">Agents</div>
          <a routerLink="/agents">View all →</a>
        </div>
        <div class="glass-card stat-card">
          <div class="stat-value">{{ stats()!.apiKeys }}</div>
          <div class="stat-label">API keys</div>
        </div>
        <div class="glass-card stat-card">
          <div class="stat-value">{{ usageTotals()?.totalTokens | number }}</div>
          <div class="stat-label">Cloud tokens (all time)</div>
          <a routerLink="/usage">Usage →</a>
        </div>
      </div>

      @if (topUsers().length) {
        <div class="glass-card section">
          <h2>Top users by tokens</h2>
          <table class="data">
            <thead>
              <tr><th>User</th><th>Requests</th><th>Tokens</th><th></th></tr>
            </thead>
            <tbody>
              @for (u of topUsers(); track u.userId) {
                <tr>
                  <td>{{ u.email || u.userId }}</td>
                  <td>{{ u.requestCount | number }}</td>
                  <td>{{ u.totalTokens | number }}</td>
                  <td><a [routerLink]="['/users', u.userId]">Open</a></td>
                </tr>
              }
            </tbody>
          </table>
        </div>
      }

      <div class="glass-card section">
        <h2>Runtime health</h2>
        <pre>{{ stats()!.system | json }}</pre>
      </div>
    }
  `,
  styles: [`
    .page-header { margin-bottom: 1rem; }
    .page-title { margin: 0; font-size: 1.5rem; }
    .page-desc { margin: 0.35rem 0 0; color: var(--text-muted); font-size: 0.9rem; }
    .muted { color: var(--text-muted); }
    .stat-card a {
      display: inline-block;
      margin-top: 0.5rem;
      font-size: 0.8rem;
      font-weight: 600;
    }
    .section { margin-top: 1rem; }
    .section h2 { margin: 0 0 0.75rem; font-size: 1rem; }
    pre {
      font-size: 0.75rem;
      overflow: auto;
      margin: 0;
      color: var(--text-secondary);
      max-height: 280px;
    }
  `],
})
export class DashboardComponent implements OnInit {
  loading = signal(true);
  error = signal<string | null>(null);
  forbidden = signal(false);
  stats = signal<any>(null);
  usageTotals = signal<{ totalTokens: number } | null>(null);
  topUsers = signal<any[]>([]);

  constructor(private api: AdminApiService) {}

  async ngOnInit(): Promise<void> {
    this.loading.set(true);
    this.error.set(null);
    this.forbidden.set(false);
    try {
      const [stats, usage] = await Promise.all([
        this.api.stats(),
        this.api.usage(50),
      ]);
      this.stats.set(stats);
      this.usageTotals.set(usage.totals);
      this.topUsers.set((usage.byUser || []).slice(0, 8));
    } catch (e: unknown) {
      const err = e as { status?: number };
      this.forbidden.set(err?.status === 403);
      this.error.set(AdminApiService.errorMessage(e));
    } finally {
      this.loading.set(false);
    }
  }
}
