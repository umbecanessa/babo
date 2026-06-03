import { Component, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { AdminApiService } from '../../core/admin-api.service';
import { ApiErrorBannerComponent } from '../../shared/api-error-banner.component';
import { formatNumber, runtimeStatusLabel, statusClass } from '../../shared/format.util';

@Component({
  selector: 'app-dashboard',
  standalone: true,
  imports: [CommonModule, RouterLink, ApiErrorBannerComponent],
  template: `
    <header class="page-header">
      <h1 class="page-title">Fleet overview</h1>
      <p class="page-desc">Database, inference spend, and live runtime plane.</p>
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

      <section class="section-label">Runtime plane</section>
      <div class="glass-card runtime-card">
        <div class="runtime-head">
          <span class="status-pill" [class]="statusClass(dash()!.runtime.status)">
            {{ dash()!.runtime.reachable ? dash()!.runtime.status : 'unreachable' }}
          </span>
          @if (dash()!.runtime.model) {
            <span class="meta">
              Model: {{ dash()!.runtime.model.loaded ? 'loaded' : 'not loaded' }}
              @if (dash()!.runtime.model.name) { · {{ dash()!.runtime.model.name }} }
            </span>
          }
        </div>
        @if (!dash()!.runtime.reachable) {
          <p class="muted warn">
            Runtime is not reachable from the API (check RUNTIME_URL on Railway). Database and token metrics still work.
          </p>
        } @else {
          <div class="runtime-grid">
            @if (dash()!.runtime.agents) {
              <div>
                <div class="mini-value">{{ dash()!.runtime.agents.active_runtimes ?? '—' }}</div>
                <div class="mini-label">Active runtimes</div>
              </div>
              <div>
                <div class="mini-value">{{ dash()!.runtime.agents.agents_in_vram ?? '—' }}</div>
                <div class="mini-label">In VRAM (alive/chat)</div>
              </div>
              <div>
                <div class="mini-value">{{ dash()!.runtime.agents.agents_sleeping ?? '—' }}</div>
                <div class="mini-label">Sleeping</div>
              </div>
            }
            @if (dash()!.runtime.analytics) {
              <div>
                <div class="mini-value">{{ dash()!.runtime.analytics.total_agents ?? '—' }}</div>
                <div class="mini-label">Agents on disk (runtime)</div>
              </div>
              <div>
                <div class="mini-value">{{ formatNumber(dash()!.runtime.analytics.total_facts) }}</div>
                <div class="mini-label">Facts in memory</div>
              </div>
              <div>
                <div class="mini-value">{{ formatNumber(dash()!.runtime.analytics.total_turns) }}</div>
                <div class="mini-label">Turns (loaded agents)</div>
              </div>
              <div>
                <div class="mini-value">{{ formatNumber(dash()!.runtime.analytics.total_sleep_cycles) }}</div>
                <div class="mini-label">Sleep cycles</div>
              </div>
            }
          </div>
          @if (statusBreakdown().length) {
            <div class="status-breakdown">
              @for (row of statusBreakdown(); track row.status) {
                <span class="chip">{{ row.status }}: {{ row.count }}</span>
              }
            </div>
          }
          @if (dash()!.runtime.sleepQueue) {
            <p class="meta-line">
              Sleep queue: pending {{ dash()!.runtime.sleepQueue.pending ?? 0 }},
              running {{ dash()!.runtime.sleepQueue.running ?? 0 }}
            </p>
          }
        }
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
              <thead><tr><th>Name</th><th>Owner</th><th>Live</th><th></th></tr></thead>
              <tbody>
                @for (a of dash()!.recentAgents; track a.id) {
                  <tr>
                    <td>{{ a.name || '—' }}</td>
                    <td>{{ a.user?.email }}</td>
                    <td>
                      <span class="status-pill sm" [class]="statusClass(runtimeStatusLabel(a))">
                        {{ runtimeStatusLabel(a) }}
                      </span>
                    </td>
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
    .runtime-card { margin-bottom: 1rem; }
    .runtime-head { display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: center; margin-bottom: 0.75rem; }
    .meta { font-size: 0.85rem; color: var(--text-secondary); }
    .warn { margin: 0.5rem 0 0; }
    .runtime-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
      gap: 1rem;
    }
    .mini-value { font-size: 1.35rem; font-weight: 700; }
    .mini-label { font-size: 0.75rem; color: var(--text-muted); }
    .status-breakdown { display: flex; flex-wrap: wrap; gap: 0.4rem; margin-top: 0.85rem; }
    .chip {
      font-size: 0.75rem;
      padding: 0.2rem 0.55rem;
      border-radius: 999px;
      background: rgba(124, 91, 245, 0.1);
      color: var(--text-secondary);
    }
    .meta-line { margin: 0.75rem 0 0; font-size: 0.82rem; color: var(--text-muted); }
    .two-col {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
      gap: 1rem;
      margin-top: 1rem;
    }
    .section h2 { margin: 0 0 0.75rem; font-size: 1rem; }
    table.compact th, table.compact td { padding: 0.45rem 0.5rem; }
    .status-pill {
      display: inline-block;
      padding: 0.2rem 0.55rem;
      border-radius: 999px;
      font-size: 0.75rem;
      font-weight: 600;
      text-transform: capitalize;
    }
    .status-pill.sm { font-size: 0.68rem; }
    .status-pill.ok { background: rgba(20, 184, 166, 0.15); color: var(--accent-success); }
    .status-pill.busy { background: rgba(124, 91, 245, 0.15); color: var(--accent-primary); }
    .status-pill.sleep { background: rgba(229, 165, 32, 0.15); color: var(--accent-warn); }
    .status-pill.bad { background: rgba(192, 57, 43, 0.12); color: var(--accent-danger); }
    .status-pill.neutral { background: rgba(0,0,0,0.06); color: var(--text-secondary); }
  `],
})
export class DashboardComponent implements OnInit {
  loading = signal(true);
  error = signal<string | null>(null);
  forbidden = signal(false);
  dash = signal<any>(null);

  formatNumber = formatNumber;
  runtimeStatusLabel = runtimeStatusLabel;
  statusClass = statusClass;

  constructor(private api: AdminApiService) {}

  statusBreakdown(): { status: string; count: number }[] {
    const by = this.dash()?.runtime?.analytics?.agents_by_status;
    if (!by || typeof by !== 'object') return [];
    return Object.entries(by).map(([status, count]) => ({
      status,
      count: count as number,
    }));
  }

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
