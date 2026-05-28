import { Component, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { AuthService } from '../../core/auth.service';
import { AdminApiService, SetupStatus } from '../../core/admin-api.service';

@Component({
  selector: 'app-setup',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  template: `
    <div class="auth-shell">
      <div class="glass-card auth-card">
        <p class="eyebrow">Internal · Operator only</p>
        <h1>Administrator setup</h1>
        @if (status()?.setupMode === 'claim') {
          <p class="sub">
            This deployment has {{ status()!.userCount }} user account(s) but no administrator yet.
            Sign in with your <strong>existing Babo account</strong> to claim the first admin role.
          </p>
        } @else {
          <p class="sub">No administrator exists. Create the first operator account for this environment.</p>
        }

        <form (ngSubmit)="submit()">
          @if (status()?.setupMode !== 'claim') {
            <div class="field">
              <label>Display name</label>
              <input [(ngModel)]="displayName" name="displayName" placeholder="Optional" />
            </div>
          }
          <div class="field">
            <label>Email</label>
            <input type="email" [(ngModel)]="email" name="email" required autocomplete="username" />
          </div>
          <div class="field">
            <label>Password</label>
            <input
              type="password"
              [(ngModel)]="password"
              name="password"
              required
              [minlength]="status()?.setupMode === 'claim' ? 1 : 8"
              autocomplete="current-password"
            />
            @if (status()?.setupMode !== 'claim') {
              <span class="hint">Minimum 8 characters for new accounts</span>
            }
          </div>
          @if (error()) { <p class="error-msg">{{ error() }}</p> }
          <button class="btn btn-primary" type="submit" [disabled]="loading() || !status()">
            {{ loading() ? 'Working…' : (status()?.setupMode === 'claim' ? 'Claim admin access' : 'Create admin account') }}
          </button>
        </form>
        <p class="login-hint">
          Already an administrator?
          <a routerLink="/login">Sign in</a>
        </p>
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
    .auth-card { width: 100%; max-width: 480px; }
    .eyebrow {
      margin: 0 0 0.35rem;
      font-size: 0.72rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--accent-primary);
    }
    h1 { margin: 0 0 0.35rem; font-size: 1.5rem; }
    .sub { color: var(--text-muted); margin: 0 0 1.5rem; font-size: 0.9rem; line-height: 1.5; }
    form .btn { width: 100%; margin-top: 0.5rem; }
    .hint { display: block; font-size: 0.75rem; color: var(--text-muted); margin-top: 0.25rem; }
    .login-hint {
      margin: 1rem 0 0;
      font-size: 0.85rem;
      color: var(--text-muted);
      text-align: center;
    }
  `],
})
export class SetupComponent implements OnInit {
  email = '';
  password = '';
  displayName = '';
  loading = signal(false);
  error = signal('');
  status = signal<SetupStatus | null>(null);

  constructor(
    private auth: AuthService,
    private router: Router,
    private api: AdminApiService,
  ) {}

  async ngOnInit(): Promise<void> {
    try {
      const s = await this.api.setupStatus();
      this.status.set(s);
      if (!s.needsSetup) {
        await this.router.navigate(['/login']);
      }
    } catch {
      this.status.set({
        needsSetup: true,
        hasAdmin: false,
        userCount: 0,
        canClaimExisting: false,
        setupMode: 'create',
      });
    }
  }

  async submit(): Promise<void> {
    this.error.set('');
    this.loading.set(true);
    try {
      await this.auth.bootstrap(
        this.email,
        this.password,
        this.status()?.setupMode === 'claim' ? undefined : this.displayName || undefined,
      );
      await this.router.navigate(['/']);
    } catch (e: unknown) {
      this.error.set(AdminApiService.errorMessage(e));
    } finally {
      this.loading.set(false);
    }
  }
}
