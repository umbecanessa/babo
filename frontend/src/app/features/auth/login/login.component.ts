import { Component, OnInit, OnDestroy, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { TranslateModule, TranslateService } from '@ngx-translate/core';
import { AuthService } from '../../../core/services/auth.service';
import { ApiService } from '../../../core/services/api.service';
import { HttpClient } from '@angular/common/http';
import { checkAuthServer } from '../auth-server.util';
import { ThemeService } from '../../../core/services/theme.service';

@Component({
  selector: 'app-login',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, TranslateModule],
  templateUrl: './login.component.html',
  styleUrl: './login.component.scss',
})
export class LoginComponent implements OnInit, OnDestroy {
  email = '';
  password = '';
  error = signal('');
  loading = signal(false);
  serverStatus = signal<'checking' | 'online' | 'offline'>('checking');
  serverMessage = signal('');

  private pollTimer: ReturnType<typeof setTimeout> | null = null;
  private pollInterval = 3000;

  constructor(
    private auth: AuthService,
    private http: HttpClient,
    private api: ApiService,
    private route: ActivatedRoute,
    public theme: ThemeService,
    private translate: TranslateService,
  ) {}

  private t(key: string): string {
    return this.translate.instant(key);
  }

  private returnUrl(): string | null {
    const q = this.route.snapshot.queryParamMap.get('returnUrl');
    return q && q.startsWith('/') ? q : null;
  }

  async ngOnInit(): Promise<void> {
    await this.api.whenReady();
    this.checkServer();
  }

  ngOnDestroy(): void {
    this.stopPolling();
  }

  checkServer(): void {
    this.serverStatus.set('checking');
    void checkAuthServer(this.api, this.http)
      .then((result) => {
        this.serverMessage.set(result.message);
        if (result.ok) {
          this.onServerOnline();
        } else {
          this.serverStatus.set('offline');
          this.startPolling();
        }
      })
      .catch(() => {
        this.serverMessage.set(this.t('auth.serverUnreachableCatch'));
        this.serverStatus.set('offline');
        this.startPolling();
      });
  }

  private onServerOnline(): void {
    this.serverStatus.set('online');
    this.stopPolling();
  }

  private startPolling(): void {
    this.stopPolling();
    this.pollTimer = setTimeout(() => this.checkServer(), this.pollInterval);
  }

  private stopPolling(): void {
    if (this.pollTimer) {
      clearTimeout(this.pollTimer);
      this.pollTimer = null;
    }
  }

  async onSubmit() {
    if (this.serverStatus() !== 'online') {
      this.error.set(this.t('auth.waitingServerStart'));
      return;
    }

    this.loading.set(true);
    this.error.set('');

    await this.api.whenReady();

    this.auth.login(this.email, this.password).subscribe({
      next: (tokens) => {
        this.auth.handleAuthSuccess(tokens, this.returnUrl());
        this.loading.set(false);
      },
      error: (err) => {
        if (err.status === 0) {
          this.serverStatus.set('offline');
          this.startPolling();
          this.error.set(this.t('auth.connectionLost'));
        } else {
          this.error.set(err.error?.message || this.t('auth.loginFailed'));
        }
        this.loading.set(false);
      },
    });
  }
}
