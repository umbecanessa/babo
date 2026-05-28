import { Component, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { AuthService } from '../../core/auth.service';
import { AdminApiService } from '../../core/admin-api.service';

@Component({
  selector: 'app-login',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  template: `
    <div class="auth-shell">
      <div class="glass-card auth-card">
        <p class="eyebrow">Internal · Operator only</p>
        <h1>Babo Admin</h1>
        @if (needsSetup()) {
          <p class="sub setup-banner">
            No administrator is configured.
            <a routerLink="/setup">Complete setup</a>
          </p>
        } @else {
          <p class="sub">Sign in with your <strong>administrator</strong> account. Regular user accounts cannot access this console.</p>
        }
        @if (sessionExpired()) {
          <p class="warn-banner">Your session ended. Sign in again.</p>
        }
        @if (forbiddenHint()) {
          <p class="warn-banner">Only administrator accounts may use this console.</p>
        }
        <form (ngSubmit)="submit()">
          <div class="field">
            <label>Email</label>
            <input type="email" [(ngModel)]="email" name="email" required autocomplete="username" />
          </div>
          <div class="field">
            <label>Password</label>
            <input type="password" [(ngModel)]="password" name="password" required autocomplete="current-password" />
          </div>
          @if (error()) { <p class="error-msg">{{ error() }}</p> }
          <button class="btn btn-primary" type="submit" [disabled]="loading()">
            {{ loading() ? 'Signing in…' : 'Sign in as administrator' }}
          </button>
        </form>
        @if (needsSetup()) {
          <a class="setup-link btn btn-ghost" routerLink="/setup">Administrator setup</a>
        }
      </div>
    </div>
  `,
  styles: [`
    .auth-shell {
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 2rem;
    }
    .auth-card { width: 100%; max-width: 420px; }
    .eyebrow {
      margin: 0 0 0.35rem;
      font-size: 0.72rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--accent-primary);
    }
    h1 { margin: 0 0 0.35rem; font-size: 1.5rem; }
    .sub { color: var(--text-muted); margin: 0 0 1.5rem; font-size: 0.9rem; line-height: 1.45; }
    .setup-banner {
      padding: 0.75rem 1rem;
      border-radius: var(--radius-md);
      background: rgba(124, 91, 245, 0.08);
      border: 1px solid rgba(124, 91, 245, 0.2);
    }
    .warn-banner {
      padding: 0.65rem 0.85rem;
      border-radius: var(--radius-md);
      margin-bottom: 1rem;
      font-size: 0.85rem;
      background: rgba(229, 165, 32, 0.12);
      border: 1px solid rgba(229, 165, 32, 0.3);
      color: var(--text-secondary);
    }
    form .btn { width: 100%; margin-top: 0.5rem; }
    .setup-link {
      display: block;
      width: 100%;
      margin-top: 0.75rem;
      text-align: center;
      text-decoration: none;
    }
  `],
})
export class LoginComponent implements OnInit {
  email = '';
  password = '';
  loading = signal(false);
  error = signal('');
  needsSetup = signal(false);
  sessionExpired = signal(false);
  forbiddenHint = signal(false);

  constructor(
    private auth: AuthService,
    private router: Router,
    private api: AdminApiService,
    private route: ActivatedRoute,
  ) {}

  async ngOnInit(): Promise<void> {
    const reason = this.route.snapshot.queryParamMap.get('reason');
    this.sessionExpired.set(reason === 'session');
    this.forbiddenHint.set(reason === 'forbidden');

    try {
      const status = await this.api.setupStatus();
      this.needsSetup.set(status.needsSetup);
      if (status.needsSetup) {
        await this.router.navigate(['/setup']);
      }
    } catch {
      this.needsSetup.set(true);
    }
  }

  async submit(): Promise<void> {
    this.error.set('');
    this.loading.set(true);
    try {
      await this.auth.login(this.email, this.password);
      await this.router.navigate(['/']);
    } catch (e: unknown) {
      this.error.set(AdminApiService.errorMessage(e));
    } finally {
      this.loading.set(false);
    }
  }
}
