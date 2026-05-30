import { Component, OnInit, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { FormsModule } from '@angular/forms';
import { AdminApiService, type AdminPlatformInfo } from '../../core/admin-api.service';
import { ApiErrorBannerComponent } from '../../shared/api-error-banner.component';
import { formatUsdCents, subscriptionStatusLabel } from '../../shared/format.util';

@Component({
  selector: 'app-user-detail',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, ApiErrorBannerComponent],
  template: `
    <a routerLink="/users" class="back">← Users</a>

    <app-api-error-banner [message]="error()" [forbidden]="forbidden()" />

    @if (loading()) {
      <p class="muted">Loading…</p>
    } @else if (user()) {
      <header class="page-header">
        <h1 class="page-title">{{ user()!.email }}</h1>
        <p class="page-desc">{{ user()!.displayName || 'No display name' }} · joined {{ user()!.createdAt | date:'medium' }}</p>
      </header>

      <div class="stat-grid">
        <div class="glass-card">
          <div class="stat-value">{{ usage()?.ledger?.totalTokens | number }}</div>
          <div class="stat-label">Total tokens</div>
        </div>
        <div class="glass-card">
          <div class="stat-value">{{ usage()?.ledger?.requestCount | number }}</div>
          <div class="stat-label">Inference requests</div>
        </div>
        <div class="glass-card">
          <div class="stat-value">{{ formatUsdCents(usage()?.ledger?.upstreamCostCents) }}</div>
          <div class="stat-label">Upstream cost</div>
        </div>
        <div class="glass-card">
          <div class="stat-value">{{ user()!.agents?.length || 0 }}</div>
          <div class="stat-label">Agents</div>
        </div>
      </div>

      @if (platform()?.billingEnabled && subscription()) {
        <div class="glass-card section billing-panel">
          <h2>Babo Cloud billing</h2>
          <div class="billing-grid">
            <div>
              <span class="field-label">Status</span>
              <span class="badge" [class.badge-admin]="subscription()!.status === 'lifetime_comp'">
                {{ subscriptionStatusLabel(subscription()!.status) }}
              </span>
            </div>
            <div>
              <span class="field-label">Plan</span>
              <span>{{ subscription()!.planId || '—' }}</span>
            </div>
            <div>
              <span class="field-label">Included usage</span>
              <span>
                @if (subscription()!.includedCreditCents > 0) {
                  {{ formatUsdCents(subscription()!.usedCreditCents) }}
                  / {{ formatUsdCents(subscription()!.includedCreditCents) }}
                  ({{ subscription()!.usedPercent }}% used)
                } @else if (subscription()!.billingExempt) {
                  Billing exempt
                } @else {
                  —
                }
              </span>
            </div>
            <div>
              <span class="field-label">GX10 brain</span>
              <span>{{ subscription()!.hostedGx10Enabled ? 'Enabled' : 'Off' }}</span>
            </div>
            @if (subscription()!.currentPeriodEnd) {
              <div>
                <span class="field-label">Period end</span>
                <span>{{ subscription()!.currentPeriodEnd | date:'mediumDate' }}</span>
              </div>
            }
          </div>

          <div class="action-row">
            @if (!isLifetime()) {
              <button class="btn btn-primary" type="button" [disabled]="billingBusy()" (click)="grantLifetime()">
                Grant lifetime (free)
              </button>
            } @else {
              <button class="btn btn-ghost danger" type="button" [disabled]="billingBusy()" (click)="revokeLifetime()">
                Revoke lifetime
              </button>
            }
            @if (platform()?.billingProvider !== 'operator' && !isLifetime() && subscription()!.status !== 'active') {
              <button class="btn btn-secondary" type="button" [disabled]="billingBusy()" (click)="activateDev()">
                Activate Cloud Basic (dev)
              </button>
            }
          </div>

          @if (!isLifetime()) {
            <div class="field-group">
              <label class="field-label">Grant note (optional)</label>
              <input class="field-input" [(ngModel)]="grantNote" placeholder="e.g. family comp, beta tester" />
            </div>
          }

          @if (billingMessage()) {
            <p class="info" [class.error]="billingError()">{{ billingMessage() }}</p>
          }
        </div>
      }

      <div class="glass-card section">
        <div class="row">
          <span class="badge" [class.badge-admin]="user()!.role === 'admin'">{{ user()!.role }}</span>
          <button class="btn btn-ghost" type="button" (click)="toggleRole()">
            {{ user()!.role === 'admin' ? 'Demote to user' : 'Promote to admin' }}
          </button>
        </div>
      </div>

      <div class="glass-card section">
        <h2>Agents</h2>
        @if (!user()!.agents?.length) {
          <p class="muted">No agents</p>
        } @else {
          <table class="data">
            <thead><tr><th>Name</th><th>Runtime ID</th><th>Status</th></tr></thead>
            <tbody>
              @for (a of user()!.agents; track a.id) {
                <tr>
                  <td>{{ a.name || '—' }}</td>
                  <td><code>{{ a.runtimeAgentId }}</code></td>
                  <td>{{ a.status }}</td>
                </tr>
              }
            </tbody>
          </table>
        }
      </div>

      <div class="glass-card section">
        <h2>Recent inference calls</h2>
        @if (!usage()?.recent?.length) {
          <p class="muted">No inference usage recorded</p>
        } @else {
          <table class="data">
            <thead>
              <tr><th>When</th><th>Model</th><th>Route</th><th>Cost</th><th>Total tokens</th></tr>
            </thead>
            <tbody>
              @for (r of usage()?.recent || []; track r.id) {
                <tr>
                  <td>{{ r.createdAt | date:'short' }}</td>
                  <td>{{ r.model }}</td>
                  <td>{{ r.route || '—' }}</td>
                  <td>{{ formatUsdCents(r.upstreamCostCents) }}</td>
                  <td>{{ r.totalTokens | number }}</td>
                </tr>
              }
            </tbody>
          </table>
        }
      </div>
    }
  `,
  styles: [`
    .back { display: inline-block; margin-bottom: 1rem; font-size: 0.9rem; }
    .page-header { margin-bottom: 1rem; }
    .page-title { margin: 0; font-size: 1.5rem; }
    .page-desc { margin: 0.35rem 0 0; color: var(--text-muted); font-size: 0.9rem; }
    .muted { color: var(--text-muted); }
    .section { margin-top: 1rem; }
    .section h2 { margin: 0 0 0.75rem; font-size: 1rem; }
    .row { display: flex; align-items: center; gap: 1rem; flex-wrap: wrap; }
    code { font-size: 0.75rem; }
    .billing-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 0.75rem 1rem;
      margin-bottom: 1rem;
    }
    .field-label {
      display: block;
      font-size: 0.72rem;
      color: var(--text-muted);
      margin-bottom: 0.15rem;
    }
    .action-row { display: flex; flex-wrap: wrap; gap: 0.5rem; margin-bottom: 0.75rem; }
    .field-group { margin-top: 0.5rem; }
    .field-input {
      width: 100%;
      max-width: 420px;
      padding: 0.45rem 0.6rem;
      border-radius: var(--radius-md);
      border: 1px solid var(--glass-border);
      background: var(--glass-bg);
      color: var(--text-primary);
    }
    .info { margin: 0.75rem 0 0; font-size: 0.85rem; color: var(--accent-success); }
    .info.error { color: var(--accent-danger); }
    .btn.danger { color: var(--accent-danger); }
  `],
})
export class UserDetailComponent implements OnInit {
  loading = signal(true);
  error = signal<string | null>(null);
  forbidden = signal(false);
  user = signal<any>(null);
  usage = signal<any>(null);
  platform = signal<AdminPlatformInfo | null>(null);
  billingBusy = signal(false);
  billingMessage = signal<string | null>(null);
  billingError = signal(false);
  grantNote = '';

  subscription = computed(() => this.usage()?.subscription ?? null);
  isLifetime = computed(() => {
    const s = this.subscription();
    return !!s && (s.billingExempt || s.status === 'lifetime_comp');
  });

  formatUsdCents = formatUsdCents;
  subscriptionStatusLabel = subscriptionStatusLabel;

  private userId = '';

  constructor(private api: AdminApiService, private route: ActivatedRoute) {}

  async ngOnInit(): Promise<void> {
    this.userId = this.route.snapshot.paramMap.get('id') || '';
    this.loading.set(true);
    try {
      const [user, usage, platform] = await Promise.all([
        this.api.user(this.userId),
        this.api.userUsage(this.userId, 50),
        this.api.platform().catch(() => null),
      ]);
      this.user.set(user);
      this.usage.set(usage);
      this.platform.set(platform);
    } catch (e: unknown) {
      const err = e as { status?: number };
      this.forbidden.set(err?.status === 403);
      this.error.set(AdminApiService.errorMessage(e));
    } finally {
      this.loading.set(false);
    }
  }

  async grantLifetime(): Promise<void> {
    if (!confirm(`Grant lifetime complimentary access to ${this.user()?.email}?`)) return;
    this.billingBusy.set(true);
    this.billingMessage.set(null);
    try {
      const sub = await this.api.grantLifetime(this.userId, this.grantNote.trim() || undefined);
      this.usage.update((u) => (u ? { ...u, subscription: sub } : u));
      this.billingMessage.set('Lifetime access granted.');
      this.billingError.set(false);
    } catch (e: unknown) {
      this.billingMessage.set(AdminApiService.errorMessage(e));
      this.billingError.set(true);
    } finally {
      this.billingBusy.set(false);
    }
  }

  async revokeLifetime(): Promise<void> {
    if (!confirm(`Revoke lifetime access for ${this.user()?.email}?`)) return;
    this.billingBusy.set(true);
    this.billingMessage.set(null);
    try {
      const sub = await this.api.revokeLifetime(this.userId);
      this.usage.update((u) => (u ? { ...u, subscription: sub } : u));
      this.billingMessage.set('Lifetime access revoked.');
      this.billingError.set(false);
    } catch (e: unknown) {
      this.billingMessage.set(AdminApiService.errorMessage(e));
      this.billingError.set(true);
    } finally {
      this.billingBusy.set(false);
    }
  }

  async activateDev(): Promise<void> {
    if (!confirm(`Activate Cloud Basic (dev, no Stripe) for ${this.user()?.email}?`)) return;
    this.billingBusy.set(true);
    this.billingMessage.set(null);
    try {
      const sub = await this.api.activateCloudBasicDev(this.userId);
      this.usage.update((u) => (u ? { ...u, subscription: sub } : u));
      this.billingMessage.set('Cloud Basic activated (dev).');
      this.billingError.set(false);
    } catch (e: unknown) {
      this.billingMessage.set(AdminApiService.errorMessage(e));
      this.billingError.set(true);
    } finally {
      this.billingBusy.set(false);
    }
  }

  async toggleRole(): Promise<void> {
    const u = this.user();
    if (!u) return;
    const next = u.role === 'admin' ? 'user' : 'admin';
    if (!confirm(`${next === 'admin' ? 'Promote' : 'Demote'} ${u.email}?`)) return;
    try {
      await this.api.updateRole(this.userId, next);
      this.user.set({ ...u, role: next });
    } catch (e: unknown) {
      alert(AdminApiService.errorMessage(e));
    }
  }
}
