import { Component, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { AuthService } from '../../core/auth.service';
import { AdminApiService, type AdminPlatformInfo } from '../../core/admin-api.service';

@Component({
  selector: 'app-admin-layout',
  standalone: true,
  imports: [CommonModule, RouterOutlet, RouterLink, RouterLinkActive],
  template: `
    <div class="layout">
      <aside class="sidebar glass-panel">
        <div class="brand">
          <span class="brand-title">Babo Admin</span>
          <span class="brand-sub">Operator console</span>
        </div>
        <nav>
          <a routerLink="/" routerLinkActive="active" [routerLinkActiveOptions]="{ exact: true }">Dashboard</a>
          <a routerLink="/users" routerLinkActive="active">Users</a>
          @if (platform()?.billingEnabled) {
            <a routerLink="/billing" routerLinkActive="active">Billing</a>
          }
          <a routerLink="/agents" routerLinkActive="active">Agents</a>
          <a routerLink="/usage" routerLinkActive="active">Token usage</a>
        </nav>
        <div class="sidebar-foot">
          @if (platform()?.billingEnabled) {
            <div class="billing-pill">Billing on · {{ platform()!.billingProvider }}</div>
          }
          @if (auth.operatorEmail) {
            <div class="operator">{{ auth.operatorEmail }}</div>
          }
          <button class="btn btn-ghost logout" type="button" (click)="logout()">Sign out</button>
        </div>
      </aside>
      <main class="content">
        <router-outlet />
      </main>
    </div>
  `,
  styles: [`
    .layout { display: flex; min-height: 100vh; }
    .sidebar {
      width: 240px;
      padding: 1.25rem 1rem;
      display: flex;
      flex-direction: column;
      margin: 1rem;
      margin-right: 0;
    }
    .brand { margin-bottom: 1.5rem; padding: 0 0.5rem; }
    .brand-title { display: block; font-weight: 700; font-size: 1.1rem; }
    .brand-sub {
      display: block;
      font-size: 0.72rem;
      color: var(--text-muted);
      margin-top: 0.15rem;
    }
    nav { display: flex; flex-direction: column; gap: 0.25rem; flex: 1; }
    nav a {
      padding: 0.55rem 0.75rem;
      border-radius: var(--radius-md);
      color: var(--text-secondary);
      text-decoration: none;
      font-size: 0.9rem;
      font-weight: 500;
    }
    nav a.active, nav a:hover {
      background: rgba(124, 91, 245, 0.12);
      color: var(--accent-primary);
    }
    .sidebar-foot { margin-top: auto; }
    .billing-pill {
      font-size: 0.72rem;
      color: var(--accent-success);
      padding: 0 0.5rem 0.5rem;
    }
    .operator {
      font-size: 0.78rem;
      color: var(--text-muted);
      padding: 0 0.5rem 0.5rem;
      word-break: break-all;
    }
    .logout { width: 100%; }
    .content { flex: 1; padding: 1rem 1.5rem 2rem; overflow: auto; max-width: 1400px; }
  `],
})
export class AdminLayoutComponent implements OnInit {
  platform = signal<AdminPlatformInfo | null>(null);

  constructor(
    public auth: AuthService,
    private api: AdminApiService,
  ) {}

  async ngOnInit(): Promise<void> {
    try {
      this.platform.set(await this.api.platform());
    } catch {
      this.platform.set(null);
    }
  }

  logout(): void {
    this.auth.logout();
    location.href = '/login';
  }
}
