import { Component, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { AuthService } from '../../core/auth.service';

@Component({
  selector: 'app-setup',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="auth-shell">
      <div class="glass-card auth-card">
        <h1>Babo Admin Setup</h1>
        <p class="sub">Create the first administrator account for this deployment.</p>
        <form (ngSubmit)="submit()">
          <div class="field">
            <label>Display name</label>
            <input [(ngModel)]="displayName" name="displayName" placeholder="Optional" />
          </div>
          <div class="field">
            <label>Email</label>
            <input type="email" [(ngModel)]="email" name="email" required />
          </div>
          <div class="field">
            <label>Password</label>
            <input type="password" [(ngModel)]="password" name="password" required minlength="8" />
          </div>
          @if (error()) { <p class="error-msg">{{ error() }}</p> }
          <button class="btn btn-primary" type="submit" [disabled]="loading()">
            {{ loading() ? 'Creating…' : 'Create admin account' }}
          </button>
        </form>
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
    h1 { margin: 0 0 0.35rem; font-size: 1.5rem; }
    .sub { color: var(--text-muted); margin: 0 0 1.5rem; font-size: 0.9rem; }
    form .btn { width: 100%; margin-top: 0.5rem; }
  `],
})
export class SetupComponent {
  email = '';
  password = '';
  displayName = '';
  loading = signal(false);
  error = signal('');

  constructor(private auth: AuthService, private router: Router) {}

  async submit(): Promise<void> {
    this.error.set('');
    this.loading.set(true);
    try {
      await this.auth.bootstrap(this.email, this.password, this.displayName || undefined);
      await this.router.navigate(['/']);
    } catch (e: any) {
      this.error.set(e?.error?.message || 'Setup failed');
    } finally {
      this.loading.set(false);
    }
  }
}
