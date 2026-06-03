import { Component, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { AdminApiService, type FunnelOverview } from '../../core/admin-api.service';
import { ApiErrorBannerComponent } from '../../shared/api-error-banner.component';
import { formatNumber } from '../../shared/format.util';

@Component({
  selector: 'app-funnel',
  standalone: true,
  imports: [CommonModule, FormsModule, ApiErrorBannerComponent],
  template: `
    <header class="page-header">
      <h1 class="page-title">UA & onboarding funnel</h1>
      <p class="page-desc">
        Anonymous product analytics from the landing page and desktop setup wizard.
        Disabled unless <code>BABO_ANALYTICS_ENABLED=true</code> on the API.
      </p>
    </header>

    <div class="toolbar glass-card">
      <label>
        Period
        <select [(ngModel)]="periodDays" (ngModelChange)="reload()">
          <option [ngValue]="7">Last 7 days</option>
          <option [ngValue]="30">Last 30 days</option>
          <option [ngValue]="90">Last 90 days</option>
        </select>
      </label>
      <button class="btn btn-ghost" type="button" (click)="reload()" [disabled]="loading()">Refresh</button>
    </div>

    <app-api-error-banner [message]="error()" [forbidden]="forbidden()" />

    @if (loading()) {
      <p class="muted">Loading funnel…</p>
    } @else if (funnel()) {
      @if (!funnel()!.enabled) {
        <div class="glass-card notice">
          <p>{{ funnel()!.message }}</p>
        </div>
      } @else if (funnel()!.message && !funnel()!.web) {
        <div class="glass-card notice">
          <p>{{ funnel()!.message }}</p>
        </div>
      } @else {
        <section class="section-label">Landing (babo.agency)</section>
        @if (funnel()!.web) {
          <div class="stat-grid">
            <div class="glass-card stat-card">
              <div class="stat-value">{{ formatNumber(funnel()!.web!.uniqueVisitors) }}</div>
              <div class="stat-label">Unique visitors</div>
            </div>
            <div class="glass-card stat-card">
              <div class="stat-value">{{ formatNumber(funnel()!.web!.pageViews) }}</div>
              <div class="stat-label">Page views</div>
            </div>
            <div class="glass-card stat-card">
              <div class="stat-value">{{ formatNumber(funnel()!.web!.ctaClicks) }}</div>
              <div class="stat-label">CTA + outbound clicks</div>
            </div>
            <div class="glass-card stat-card">
              <div class="stat-value">{{ formatNumber(funnel()!.web!.outboundClicks) }}</div>
              <div class="stat-label">Outbound (GitHub, Discord)</div>
            </div>
          </div>

          <div class="two-col">
            <div class="glass-card section">
              <h2>CTA breakdown</h2>
              @if (!funnel()!.web!.ctaByLocation.length) {
                <p class="muted">No click events yet.</p>
              } @else {
                <table class="data compact">
                  <thead><tr><th>Location</th><th>Clicks</th><th>Unique</th></tr></thead>
                  <tbody>
                    @for (row of funnel()!.web!.ctaByLocation; track row.location) {
                      <tr>
                        <td><code>{{ row.location }}</code></td>
                        <td>{{ row.count | number }}</td>
                        <td>{{ row.uniqueVisitors | number }}</td>
                      </tr>
                    }
                  </tbody>
                </table>
              }
            </div>

            <div class="glass-card section">
              <h2>Audience</h2>
              @if (!funnel()!.web!.audiences.length) {
                <p class="muted">No audience data yet.</p>
              } @else {
                <table class="data compact">
                  <thead><tr><th>Variant</th><th>Visitors</th></tr></thead>
                  <tbody>
                    @for (row of funnel()!.web!.audiences; track row.audience) {
                      <tr>
                        <td>{{ row.audience }}</td>
                        <td>{{ row.uniqueVisitors | number }}</td>
                      </tr>
                    }
                  </tbody>
                </table>
              }
            </div>
          </div>

          <div class="glass-card section">
            <h2>Campaigns (UTM)</h2>
            @if (!funnel()!.web!.campaigns.length) {
              <p class="muted">No campaign-tagged traffic yet. Use <code>?utm_source=</code> on ad URLs.</p>
            } @else {
              <table class="data">
                <thead>
                  <tr><th>Campaign</th><th>Source</th><th>Views</th><th>Visitors</th></tr>
                </thead>
                <tbody>
                  @for (row of funnel()!.web!.campaigns; track row.campaign + row.source) {
                    <tr>
                      <td>{{ row.campaign }}</td>
                      <td>{{ row.source }}</td>
                      <td>{{ row.pageViews | number }}</td>
                      <td>{{ row.uniqueVisitors | number }}</td>
                    </tr>
                  }
                </tbody>
              </table>
            }
          </div>
        }

        @if (funnel()!.attribution) {
          <section class="section-label">Landing → install attribution</section>
          <div class="stat-grid">
            <div class="glass-card stat-card">
              <div class="stat-value">{{ formatNumber(funnel()!.attribution!.handoffsCreated) }}</div>
              <div class="stat-label">Download handoffs</div>
            </div>
            <div class="glass-card stat-card">
              <div class="stat-value">{{ formatNumber(funnel()!.attribution!.handoffsClaimed) }}</div>
              <div class="stat-label">Claimed in app</div>
            </div>
            <div class="glass-card stat-card">
              <div class="stat-value">{{ formatNumber(funnel()!.attribution!.claimToSetupStarted) }}</div>
              <div class="stat-label">Claimed → setup started</div>
            </div>
            <div class="glass-card stat-card">
              <div class="stat-value">{{ formatNumber(funnel()!.attribution!.claimToCompleted) }}</div>
              <div class="stat-label">Claimed → completed</div>
            </div>
          </div>
          @if (funnel()!.attribution!.byCampaign.length) {
            <div class="glass-card section">
              <h2>UTM → install (claimed)</h2>
              <table class="data">
                <thead>
                  <tr><th>Campaign</th><th>Handoffs</th><th>Claimed</th><th>Completed</th></tr>
                </thead>
                <tbody>
                  @for (row of funnel()!.attribution!.byCampaign; track row.campaign) {
                    <tr>
                      <td>{{ row.campaign }}</td>
                      <td>{{ row.handoffs | number }}</td>
                      <td>{{ row.claimed | number }}</td>
                      <td>{{ row.completed | number }}</td>
                    </tr>
                  }
                </tbody>
              </table>
            </div>
          }
        }

        <section class="section-label">Desktop setup wizard</section>
        @if (funnel()!.app) {
          <div class="stat-grid">
            <div class="glass-card stat-card">
              <div class="stat-value">{{ formatNumber(funnel()!.app!.setupStarted) }}</div>
              <div class="stat-label">Setup started</div>
            </div>
            <div class="glass-card stat-card">
              <div class="stat-value">{{ formatNumber(funnel()!.app!.setupCompleted) }}</div>
              <div class="stat-label">Setup completed</div>
            </div>
            <div class="glass-card stat-card">
              <div class="stat-value">
                {{ funnel()!.app!.completionRate != null ? funnel()!.app!.completionRate + '%' : '—' }}
              </div>
              <div class="stat-label">Completion rate</div>
            </div>
            <div class="glass-card stat-card">
              <div class="stat-value">{{ formatNumber(funnel()!.app!.billingActivated) }}</div>
              <div class="stat-label">Billing activated</div>
            </div>
          </div>

          <div class="glass-card section">
            <h2>Step funnel (cumulative)</h2>
            <p class="muted subhead">
              Installs that reached at least this step (max progress) · drop-off vs the previous step
            </p>
            <table class="data">
              <thead>
                <tr><th>Step</th><th>Reached</th><th>Unique installs</th><th>Drop-off</th></tr>
              </thead>
              <tbody>
                @for (row of funnel()!.app!.steps; track row.step) {
                  <tr>
                    <td><code>{{ row.step }}</code></td>
                    <td>{{ row.views | number }}</td>
                    <td>{{ row.uniqueInstalls | number }}</td>
                    <td>
                      @if (row.dropOffFromPrevious != null) {
                        <span [class.warn]="row.dropOffFromPrevious > 40">{{ row.dropOffFromPrevious }}%</span>
                      } @else {
                        —
                      }
                    </td>
                  </tr>
                }
              </tbody>
            </table>
          </div>

          <div class="glass-card section">
            <h2>App events</h2>
            @if (!funnel()!.app!.events.length) {
              <p class="muted">No app events yet.</p>
            } @else {
              <table class="data compact">
                <thead><tr><th>Event</th><th>Count</th><th>Unique</th></tr></thead>
                <tbody>
                  @for (row of funnel()!.app!.events; track row.name) {
                    <tr>
                      <td><code>{{ row.name }}</code></td>
                      <td>{{ row.count | number }}</td>
                      <td>{{ row.uniqueVisitors | number }}</td>
                    </tr>
                  }
                </tbody>
              </table>
            }
          </div>
        }
      }
    }
  `,
  styles: [`
    .toolbar {
      display: flex;
      align-items: end;
      gap: 1rem;
      padding: 0.85rem 1rem;
      margin-bottom: 1rem;
    }
    .toolbar label {
      display: flex;
      flex-direction: column;
      gap: 0.35rem;
      font-size: 0.78rem;
      color: var(--text-muted);
    }
    select {
      min-width: 140px;
      padding: 0.4rem 0.55rem;
      border-radius: var(--radius-md);
      border: 1px solid var(--border-subtle);
      background: var(--surface-elevated);
      color: var(--text-primary);
    }
    .notice { padding: 1rem 1.15rem; color: var(--text-secondary); }
    .section-label {
      font-size: 0.72rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--text-muted);
      margin: 1.25rem 0 0.65rem;
    }
    .two-col {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 1rem;
      margin-bottom: 1rem;
    }
    .section { margin-bottom: 1rem; }
    .section h2 { margin: 0 0 0.35rem; font-size: 1rem; }
    .subhead { margin: 0 0 0.75rem; font-size: 0.82rem; }
    table.compact th, table.compact td { padding: 0.45rem 0.5rem; }
    code { font-size: 0.82em; }
    .warn { color: var(--accent-warn); font-weight: 600; }
  `],
})
export class FunnelComponent implements OnInit {
  loading = signal(true);
  error = signal<string | null>(null);
  forbidden = signal(false);
  funnel = signal<FunnelOverview | null>(null);
  periodDays = 30;

  formatNumber = formatNumber;

  constructor(private api: AdminApiService) {}

  async ngOnInit(): Promise<void> {
    await this.reload();
  }

  async reload(): Promise<void> {
    this.loading.set(true);
    this.error.set(null);
    try {
      this.funnel.set(await this.api.funnel(this.periodDays));
    } catch (e: unknown) {
      const err = e as { status?: number };
      this.forbidden.set(err?.status === 403);
      this.error.set(AdminApiService.errorMessage(e));
    } finally {
      this.loading.set(false);
    }
  }
}
