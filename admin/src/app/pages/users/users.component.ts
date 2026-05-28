import { Component, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { AdminApiService } from '../../core/admin-api.service';

@Component({
  selector: 'app-users',
  standalone: true,
  imports: [CommonModule, RouterLink],
  template: `
    <h1 class="page-title">Users</h1>
    @if (loading()) {
      <p class="muted">Loading…</p>
    } @else {
      <div class="glass-card table-wrap">
        <table class="data">
          <thead>
            <tr>
              <th>Email</th>
              <th>Name</th>
              <th>Role</th>
              <th>Agents</th>
              <th>API keys</th>
              <th>Created</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            @for (u of users(); track u.id) {
              <tr>
                <td>{{ u.email }}</td>
                <td>{{ u.displayName || '—' }}</td>
                <td>
                  <span class="badge" [class.badge-admin]="u.role === 'admin'" [class.badge-user]="u.role !== 'admin'">
                    {{ u.role }}
                  </span>
                </td>
                <td>{{ u.agentCount }}</td>
                <td>{{ u.apiKeyCount }}</td>
                <td>{{ u.createdAt | date:'mediumDate' }}</td>
                <td><a [routerLink]="['/users', u.id]">View</a></td>
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
    table.data { min-width: 640px; }
  `],
})
export class UsersComponent implements OnInit {
  loading = signal(true);
  users = signal<any[]>([]);

  constructor(private api: AdminApiService) {}

  async ngOnInit(): Promise<void> {
    try {
      this.users.set(await this.api.users());
    } finally {
      this.loading.set(false);
    }
  }
}
