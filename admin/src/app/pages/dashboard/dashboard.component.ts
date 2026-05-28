import { Component, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { AdminApiService } from '../../core/admin-api.service';

@Component({
  selector: 'app-dashboard',
  standalone: true,
  imports: [CommonModule],
  template: `
    <h1 class="page-title">Dashboard</h1>
    @if (loading()) {
      <p class="muted">Loading…</p>
    } @else if (stats()) {
      <div class="stat-grid">
        <div class="glass-card">
          <div class="stat-value">{{ stats()!.users }}</div>
          <div class="stat-label">Users</div>
        </div>
        <div class="glass-card">
          <div class="stat-value">{{ stats()!.agents }}</div>
          <div class="stat-label">Agents</div>
        </div>
        <div class="glass-card">
          <div class="stat-value">{{ stats()!.apiKeys }}</div>
          <div class="stat-label">API keys</div>
        </div>
        <div class="glass-card">
          <div class="stat-value">{{ usageTotals()?.totalTokens | number }}</div>
          <div class="stat-label">Cloud tokens (all time)</div>
        </div>
      </div>
      <div class="glass-card section">
        <h2>Runtime health</h2>
        <pre>{{ stats()!.system | json }}</pre>
      </div>
    }
  `,
  styles: [`
    .page-title { margin: 0 0 1.25rem; font-size: 1.5rem; }
    .muted { color: var(--text-muted); }
    .section { margin-top: 1.25rem; }
    .section h2 { margin: 0 0 0.75rem; font-size: 1rem; }
    pre { font-size: 0.75rem; overflow: auto; margin: 0; color: var(--text-secondary); }
  `],
})
export class DashboardComponent implements OnInit {
  loading = signal(true);
  stats = signal<any>(null);
  usageTotals = signal<{ totalTokens: number } | null>(null);

  constructor(private api: AdminApiService) {}

  async ngOnInit(): Promise<void> {
    try {
      const [stats, usage] = await Promise.all([
        this.api.stats(),
        this.api.usage(10),
      ]);
      this.stats.set(stats);
      this.usageTotals.set(usage.totals);
    } finally {
      this.loading.set(false);
    }
  }
}
