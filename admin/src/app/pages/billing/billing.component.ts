import { Component, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { AdminApiService } from '../../core/admin-api.service';
import { ApiErrorBannerComponent } from '../../shared/api-error-banner.component';
import { formatUsdCents, subscriptionStatusLabel } from '../../shared/format.util';

@Component({
  selector: 'app-billing',
  standalone: true,
  imports: [CommonModule, RouterLink, ApiErrorBannerComponent],
  template: `
    <header class="page-header">
      <h1 class="page-title">Billing</h1>
      <p class="page-desc">
        Babo Cloud subscriptions, complimentary access, and usage pools.
        Provider: <code>{{ platform()?.billingProvider || '—' }}</code>
      </p>
    </header>

    <app-api-error-banner [message]="error()" [forbidden]="forbidden()" />

    @if (loading()) {
      <p class="muted">Loading…</p>
    } @else if (data()) {
      <div class="stat-grid">
        <div class="glass-card stat-card">
          <div class="stat-value">{{ data()!.summary.total }}</div>
          <div class="stat-label">Subscription rows</div>
        </div>
        <div class="glass-card stat-card">
          <div class="stat-value">{{ data()!.summary.activePaid }}</div>
          <div class="stat-label">Active paid</div>
        </div>
        <div class="glass-card stat-card">
          <div class="stat-value">{{ data()!.summary.lifetimeComp }}</div>
          <div class="stat-label">Lifetime / comp</div>
        </div>
        <div class="glass-card stat-card">
          <div class="stat-value">{{ data()!.summary.byStatus['none'] || 0 }}</div>
          <div class="stat-label">Not subscribed</div>
        </div>
      </div>

      <div class="glass-card section">
        <h2>All subscriptions</h2>
        @if (!data()!.subscriptions.length) {
          <p class="muted">No subscription rows yet.</p>
        } @else {
          <div class="table-wrap">
            <table class="data">
              <thead>
                <tr>
                  <th>User</th>
                  <th>Status</th>
                  <th>Plan</th>
                  <th>Usage</th>
                  <th>GX10</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                @for (row of data()!.subscriptions; track row.userId) {
                  <tr>
                    <td>
                      <div>{{ row.email }}</div>
                      @if (row.displayName) {
                        <div class="sub">{{ row.displayName }}</div>
                      }
                    </td>
                    <td>
                      <span class="badge" [class.badge-admin]="row.subscription.status === 'lifetime_comp'">
                        {{ subscriptionStatusLabel(row.subscription.status) }}
                      </span>
                    </td>
                    <td>{{ row.subscription.planId || '—' }}</td>
                    <td>
                      @if (row.subscription.includedCreditCents > 0) {
                        {{ formatUsdCents(row.subscription.usedCreditCents) }}
                        / {{ formatUsdCents(row.subscription.includedCreditCents) }}
                        ({{ row.subscription.usedPercent }}%)
                      } @else if (row.subscription.billingExempt) {
                        Exempt
                      } @else {
                        —
                      }
                    </td>
                    <td>{{ row.subscription.hostedGx10Enabled ? 'Yes' : '—' }}</td>
                    <td><a [routerLink]="['/users', row.userId]">Manage</a></td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
        }
      </div>

      <div class="glass-card section hint">
        <h2>Admin actions</h2>
        <ul>
          <li><strong>Lifetime comp</strong> — free Babo Cloud + GX10 brain; no Stripe, no pool debit.</li>
          <li><strong>Revoke lifetime</strong> — user must subscribe via Stripe Checkout.</li>
          @if (platform()?.billingProvider !== 'operator') {
            <li><strong>Activate Cloud Basic (dev)</strong> — simulates paid plan without Stripe.</li>
          }
        </ul>
        <p class="muted">Open a user’s detail page to grant or revoke access.</p>
      </div>
    }
  `,
  styles: [`
    .page-header { margin-bottom: 1rem; }
    .page-title { margin: 0; font-size: 1.5rem; }
    .page-desc { margin: 0.35rem 0 0; color: var(--text-muted); font-size: 0.9rem; }
    .page-desc code { font-size: 0.85em; }
    .muted { color: var(--text-muted); }
    .section { margin-top: 1rem; }
    .section h2 { margin: 0 0 0.75rem; font-size: 1rem; }
    .table-wrap { overflow-x: auto; }
    .sub { font-size: 0.78rem; color: var(--text-muted); }
    .hint ul { margin: 0.5rem 0; padding-left: 1.2rem; color: var(--text-secondary); font-size: 0.9rem; }
    .stat-card .stat-value { font-size: 1.5rem; font-weight: 700; }
    .stat-label { font-size: 0.75rem; color: var(--text-muted); margin-top: 0.2rem; }
  `],
})
export class BillingComponent implements OnInit {
  loading = signal(true);
  error = signal<string | null>(null);
  forbidden = signal(false);
  data = signal<any>(null);
  platform = signal<import('../../core/admin-api.service').AdminPlatformInfo | null>(null);

  formatUsdCents = formatUsdCents;
  subscriptionStatusLabel = subscriptionStatusLabel;

  constructor(private api: AdminApiService) {}

  async ngOnInit(): Promise<void> {
    this.loading.set(true);
    try {
      const [platform, data] = await Promise.all([
        this.api.platform(),
        this.api.billingSubscriptions(),
      ]);
      this.platform.set(platform);
      this.data.set(data);
    } catch (e: unknown) {
      const err = e as { status?: number };
      this.forbidden.set(err?.status === 403);
      this.error.set(AdminApiService.errorMessage(e));
    } finally {
      this.loading.set(false);
    }
  }
}
