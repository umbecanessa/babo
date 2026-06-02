import { Injectable, Injector, signal, computed } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Router } from '@angular/router';
import { Observable, tap, catchError, throwError, map } from 'rxjs';
import { AuthTokens } from '../models/user.model';
import { ApiService } from './api.service';
import { WebSocketService } from './websocket.service';
import { ChatUiSnapshotService } from './chat-ui-snapshot.service';

@Injectable({ providedIn: 'root' })
export class AuthService {
  private tokenSignal = signal<string | null>(this.getStoredToken());

  isAuthenticated = computed(() => !!this.tokenSignal());
  token = computed(() => this.tokenSignal());

  constructor(
    private http: HttpClient,
    private router: Router,
    private injector: Injector,
    private api: ApiService,
  ) {}

  private get API(): string {
    return this.api.apiBase;
  }

  register(email: string, password: string, displayName?: string) {
    return this.http.post<AuthTokens>(`${this.API}/auth/register`, {
      email, password, displayName,
    });
  }

  login(email: string, password: string) {
    return this.http.post<AuthTokens>(`${this.API}/auth/login`, {
      email, password,
    });
  }

  /**
   * Store session tokens.
   * @param redirectTo `false` = stay on current route; string = navigate there; default = dashboard
   */
  applyTokens(tokens: AuthTokens, redirectTo: false | string = '/dashboard'): void {
    localStorage.setItem('access_token', tokens.accessToken);
    localStorage.setItem('refresh_token', tokens.refreshToken);
    localStorage.setItem('user_id', tokens.userId);
    this.tokenSignal.set(tokens.accessToken);
    if (redirectTo === false) return;
    if (redirectTo) {
      void this.router.navigateByUrl(redirectTo);
    }
  }

  handleAuthSuccess(tokens: AuthTokens, returnUrl?: string | null): void {
    this.applyTokens(tokens, returnUrl || '/dashboard');
  }

  logout(): void {
    try {
      this.injector.get(WebSocketService).disconnect();
      this.injector.get(ChatUiSnapshotService).clearAll();
    } catch {
      /* optional during bootstrap */
    }
    localStorage.removeItem('access_token');
    localStorage.removeItem('refresh_token');
    localStorage.removeItem('user_id');
    this.tokenSignal.set(null);
    this.router.navigate(['/auth/login']);
  }

  getAccessToken(): string | null {
    return this.tokenSignal();
  }

  getRefreshToken(): string | null {
    return localStorage.getItem('refresh_token');
  }

  getUserId(): string | null {
    return localStorage.getItem('user_id');
  }

  /**
   * Refresh the access token using the stored refresh token.
   * On success: stores new tokens and emits the new access token.
   * On failure: logs the user out and propagates the error.
   */
  refreshAccessToken(): Observable<string> {
    const refreshToken = this.getRefreshToken();
    if (!refreshToken) {
      this.logout();
      return throwError(() => new Error('No refresh token'));
    }

    return this.http
      .post<AuthTokens>(`${this.API}/auth/refresh`, { refreshToken })
      .pipe(
        tap((tokens) => {
          localStorage.setItem('access_token', tokens.accessToken);
          localStorage.setItem('refresh_token', tokens.refreshToken);
          this.tokenSignal.set(tokens.accessToken);
        }),
        map((tokens) => tokens.accessToken),
        catchError((err) => {
          this.logout();
          return throwError(() => err);
        }),
      );
  }

  private getStoredToken(): string | null {
    return localStorage.getItem('access_token');
  }
}
