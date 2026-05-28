import { Component, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { AdminApiService } from '../../core/admin-api.service';

@Component({
  selector: 'app-agents',
  standalone: true,
  imports: [CommonModule],
  template: `
    <h1 class="page-title">Agents</h1>
    @if (loading()) {
      <p class="muted">Loading…</p>
    } @else {
      <div class="glass-card table-wrap">
        <table class="data">
          <thead>
            <tr>
              <th>Name</th>
              <th>Owner</th>
              <th>Runtime ID</th>
              <th>DB status</th>
              <th>Runtime</th>
              <th>Created</th>
            </tr>
          </thead>
          <tbody>
            @for (a of agents(); track a.id) {
              <tr>
                <td>{{ a.name || '—' }}</td>
                <td>{{ a.user?.email }}</td>
                <td><code>{{ a.runtimeAgentId }}</code></td>
                <td>{{ a.status }}</td>
                <td>{{ a.runtime?.status || a.runtime?.agent_status || '—' }}</td>
                <td>{{ a.createdAt | date:'mediumDate' }}</td>
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
    .table-wrap { overflow-x: auto; padding: 0; }
    code { font-size: 0.72rem; }
  `],
})
export class AgentsComponent implements OnInit {
  loading = signal(true);
  agents = signal<any[]>([]);

  constructor(private api: AdminApiService) {}

  async ngOnInit(): Promise<void> {
    try {
      this.agents.set(await this.api.agents());
    } finally {
      this.loading.set(false);
    }
  }
}
