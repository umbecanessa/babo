import { Component, OnInit, OnDestroy, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { HttpClient } from '@angular/common/http';
import { AuthService } from '../../../core/services/auth.service';
import { environment } from '../../../../environments/environment';

@Component({
  selector: 'app-register',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './register.component.html',
  styleUrl: './register.component.scss',
})
export class RegisterComponent implements OnInit, OnDestroy {
  email = '';
  password = '';
  displayName = '';
  error = signal('');
  loading = signal(false);
  serverStatus = signal<'checking' | 'online' | 'offline'>('checking');

  particleCount = Array.from({ length: 20 }, (_, i) => i);
  private pollTimer: ReturnType<typeof setTimeout> | null = null;
  private pollInterval = 3000;

  constructor(
    private auth: AuthService,
    private http: HttpClient,
  ) {}

  ngOnInit(): void {
    this.checkServer();
  }

  ngOnDestroy(): void {
    this.stopPolling();
  }

  particleX(i: number): string {
    return ((i * 37 + 13) % 100) + '%';
  }

  particleSize(i: number): string {
    return (2 + (i % 3)) + 'px';
  }

  checkServer(): void {
    this.serverStatus.set('checking');
    this.http.get(`${environment.apiUrl}/auth/login`, { observe: 'response' }).subscribe({
      next: () => this.onServerOnline(),
      error: (err) => {
        if (err.status > 0) {
          this.onServerOnline();
        } else {
          this.serverStatus.set('offline');
          this.startPolling();
        }
      },
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

  onSubmit() {
    if (this.serverStatus() !== 'online') {
      this.error.set('Waiting for server to start...');
      return;
    }

    this.loading.set(true);
    this.error.set('');

    this.auth.register(this.email, this.password, this.displayName || undefined).subscribe({
      next: (tokens) => {
        this.auth.handleAuthSuccess(tokens);
        this.loading.set(false);
      },
      error: (err) => {
        if (err.status === 0) {
          this.serverStatus.set('offline');
          this.startPolling();
          this.error.set('Server connection lost. Retrying...');
        } else {
          this.error.set(err.error?.message || 'Registration failed');
        }
        this.loading.set(false);
      },
    });
  }
}
