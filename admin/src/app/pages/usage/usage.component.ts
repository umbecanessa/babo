import { Component, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { AdminApiService } from '../../core/admin-api.service';

@Component({
  selector: 'app-usage',
  standalone: true,
  imports: [CommonModule, RouterLink],
  template: `
    <h1 class="page-title">Token usage</h1>
    @if (loading()) {
      <p class="muted">Loading…</p>
    } @else if (data()) {
      <div class="stat-grid">
        <div class="glass-card">
          <div class="stat-value">{{ data()!.totals.totalTokens | number }}</div>
          <div class="stat-label">Total tokens</div>
        </div>
        <div class="glass-card">
          <div class="stat-value">{{ data()!.totals.promptTokens | number }}</div>
          <div class="stat-label">Prompt tokens</div>
        </div>
        <div class="glass-card">
          <div class="stat-value">{{ data()!.totals.completionTokens | number }}</div>
          <div class="stat-label">Completion tokens</div>
        </div>
        <div class="glass-card">
          <div class="stat-value">{{ data()!.totals.requestCount | number }}</div>
          <div class="stat-label">Requests</div>
        </div>
      </div>

      <div class="glass-card section">
        <h2>By user</h2>
        <table class="data">
          <thead>
            <tr><th>User</th><th>Requests</th><th>Total tokens</th><th></th></tr>
          </thead>
          <tbody>
            @for (u of data()!.byUser; track u.userId) {
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
        <table class="data">
          <thead>
            <tr><th>When</th><th>User</th><th>Model</th><th>Prompt</th><th>Out</th><th>Total</th></tr>
          </thead>
          <tbody>
            @for (r of data()!.recent; track r.id) {
              <tr>
                <td>{{ r.createdAt | date:'short' }}</td>
                <td>{{ r.userEmail || '—' }}</td>
                <td>{{ r.model }}</td>
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
    .page-title { margin: 0 0 1.25rem; font-size: 1.5rem; }
    .muted { color: var(--text-muted); }
    .section { margin-top: 1rem; }
    .section h2 { margin: 0 0 0.75rem; font-size: 1rem; }
  `],
})
export class UsageComponent implements OnInit {
  loading = signal(true);
  data = signal<any>(null);

  constructor(private api: AdminApiService) {}

  async ngOnInit(): Promise<void> {
    try {
      this.data.set(await this.api.usage(100));
    } finally {
      this.loading.set(false);
    }
  }
}
