import { Component, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { AdminApiService } from '../../core/admin-api.service';
import { ApiErrorBannerComponent } from '../../shared/api-error-banner.component';
import { formatNumber } from '../../shared/format.util';

@Component({
  selector: 'app-dashboard',
  standalone: true,
  imports: [CommonModule, RouterLink, ApiErrorBannerComponent],
  template: `
    <header class="page-header">
      <h1 class="page-title">Fleet overview</h1>
      <p class="page-desc">Database counts, inference spend, and product funnel.</p>
    </header>

    <app-api-error-banner [message]="error()" [forbidden]="forbidden()" />

    @if (loading()) {
      <p class="muted">Loading fleet data…</p>
    } @else if (dash()) {
      <section class="section-label">Platform (database)</section>
      <div class="stat-grid">
        <div class="glass-card stat-card">
          <div class="stat-value">{{ dash()!.database.users }}</div>
          <div class="stat-label">Users</div>
          <a routerLink="/users">Manage</a>
        </div>
        <div class="glass-card stat-card">
          <div class="stat-value">{{ dash()!.database.admins }}</div>
          <div class="stat-label">Administrators</div>
        </div>
        <div class="glass-card stat-card">
          <div class="stat-value">{{ dash()!.database.agents }}</div>
          <div class="stat-label">Agents (DB)</div>
          <a routerLink="/agents">View fleet</a>
        </div>
        <div class="glass-card stat-card">
          <div class="stat-value">{{ dash()!.database.apiKeys }}</div>
          <div class="stat-label">API keys</div>
        </div>
        <div class="glass-card stat-card">
          <div class="stat-value">→</div>
          <div class="stat-label">UA funnel</div>
          <a routerLink="/funnel">Landing & setup</a>
        </div>
      </div>

      <section class="section-label">Inference (Babo Cloud ledger)</section>
      <div class="stat-grid">
        <div class="glass-card stat-card">
          <div class="stat-value">{{ formatNumber(dash()!.inference.allTime.totalTokens) }}</div>
          <div class="stat-label">Tokens (all time)</div>
          <a routerLink="/usage">Usage details</a>
        </div>
        <div class="glass-card stat-card">
          <div class="stat-value">{{ formatNumber(dash()!.inference.allTime.requestCount) }}</div>
          <div class="stat-label">Requests (all time)</div>
        </div>
        <div class="glass-card stat-card">
          <div class="stat-value">{{ formatNumber(dash()!.inference.last24h.totalTokens) }}</div>
          <div class="stat-label">Tokens (24h)</div>
        </div>
        <div class="glass-card stat-card">
          <div class="stat-value">{{ formatNumber(dash()!.inference.last24h.requestCount) }}</div>
          <div class="stat-label">Requests (24h)</div>
        </div>
      </div>

      <div class="two-col">
        <div class="glass-card section">
          <h2>Top users by tokens</h2>
          @if (!dash()!.topUsersByTokens?.length) {
            <p class="muted">No inference usage yet.</p>
          } @else {
            <table class="data compact">
              <thead><tr><th>User</th><th>Tokens</th><th></th></tr></thead>
              <tbody>
                @for (u of dash()!.topUsersByTokens; track u.userId) {
                  <tr>
                    <td>{{ u.email || u.userId }}</td>
                    <td>{{ formatNumber(u.totalTokens) }}</td>
                    <td><a [routerLink]="['/users', u.userId]">Open</a></td>
                  </tr>
                }
              </tbody>
            </table>
          }
        </div>

        <div class="glass-card section">
          <h2>Recent agents</h2>
          @if (!dash()!.recentAgents?.length) {
            <p class="muted">No agents registered.</p>
          } @else {
            <table class="data compact">
              <thead><tr><th>Name</th><th>Owner</th><th></th></tr></thead>
              <tbody>
                @for (a of dash()!.recentAgents; track a.id) {
                  <tr>
                    <td>{{ a.name || '—' }}</td>
                    <td>{{ a.user?.email }}</td>
                    <td><a [routerLink]="['/agents', a.id]">Ops</a></td>
                  </tr>
                }
              </tbody>
            </table>
          }
        </div>
      </div>
    }
  `,
  styles: [`
    .section-label {
      font-size: 0.72rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--text-muted);
      margin: 1.25rem 0 0.65rem;
    }
    .stat-card a { display: inline-block; margin-top: 0.45rem; font-size: 0.8rem; font-weight: 600; }
    .two-col {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
      gap: 1rem;
      margin-top: 1rem;
    }
    .section h2 { margin: 0 0 0.75rem; font-size: 1rem; }
    table.compact th, table.compact td { padding: 0.45rem 0.5rem; }
  `],
})
export class DashboardComponent implements OnInit {
  loading = signal(true);
  error = signal<string | null>(null);
  forbidden = signal(false);
  dash = signal<any>(null);

  formatNumber = formatNumber;

  constructor(private api: AdminApiService) {}

  async ngOnInit(): Promise<void> {
    this.loading.set(true);
    try {
      this.dash.set(await this.api.dashboard());
    } catch (e: unknown) {
      const err = e as { status?: number };
      this.forbidden.set(err?.status === 403);
      this.error.set(AdminApiService.errorMessage(e));
    } finally {
      this.loading.set(false);
    }
  }
}
