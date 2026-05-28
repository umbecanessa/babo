import { Component, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { AdminApiService } from '../../core/admin-api.service';
import { ApiErrorBannerComponent } from '../../shared/api-error-banner.component';

@Component({
  selector: 'app-user-detail',
  standalone: true,
  imports: [CommonModule, RouterLink, ApiErrorBannerComponent],
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
          <div class="stat-value">{{ user()!.agents?.length || 0 }}</div>
          <div class="stat-label">Agents</div>
        </div>
        @if (usage()?.user?.cloudSubscription) {
          <div class="glass-card">
            <div class="stat-value">{{ usage()!.user.cloudSubscription.plan || '—' }}</div>
            <div class="stat-label">Cloud plan</div>
          </div>
        }
      </div>

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
              <tr><th>When</th><th>Model</th><th>Route</th><th>Prompt</th><th>Completion</th><th>Total</th></tr>
            </thead>
            <tbody>
              @for (r of usage()?.recent || []; track r.id) {
                <tr>
                  <td>{{ r.createdAt | date:'short' }}</td>
                  <td>{{ r.model }}</td>
                  <td>{{ r.route || '—' }}</td>
                  <td>{{ r.promptTokens | number }}</td>
                  <td>{{ r.completionTokens | number }}</td>
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
  `],
})
export class UserDetailComponent implements OnInit {
  loading = signal(true);
  error = signal<string | null>(null);
  forbidden = signal(false);
  user = signal<any>(null);
  usage = signal<any>(null);
  private userId = '';

  constructor(private api: AdminApiService, private route: ActivatedRoute) {}

  async ngOnInit(): Promise<void> {
    this.userId = this.route.snapshot.paramMap.get('id') || '';
    this.loading.set(true);
    try {
      const [user, usage] = await Promise.all([
        this.api.user(this.userId),
        this.api.userUsage(this.userId, 50),
      ]);
      this.user.set(user);
      this.usage.set(usage);
    } catch (e: unknown) {
      const err = e as { status?: number };
      this.forbidden.set(err?.status === 403);
      this.error.set(AdminApiService.errorMessage(e));
    } finally {
      this.loading.set(false);
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
