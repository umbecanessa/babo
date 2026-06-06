import { Injectable, Injector, signal, computed } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Router } from '@angular/router';
import { Observable, tap, catchError, throwError, map, firstValueFrom } from 'rxjs';
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
    this.scheduleCloudInferenceAuthSync();
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
    this.scheduleCloudInferenceAuthClear();
    this.router.navigate(['/auth/login']);
  }

  getAccessToken(): string | null {
    return this.tokenSignal();
  }

  getRefreshToken(): string | null {
    return localStorage.getItem('refresh_token');
  }

  /** True when access token is missing or past ``exp`` (30s skew). */
  isAccessTokenExpired(): boolean {
    const token = this.getAccessToken();
    if (!token) return true;
    try {
      const payload = JSON.parse(atob(token.split('.')[1]));
      const exp = payload?.exp;
      if (typeof exp !== 'number') return false;
      return Date.now() / 1000 >= exp - 30;
    } catch {
      return false;
    }
  }

  /** Refresh when expired; returns current access token or null after logout. */
  async ensureFreshAccessToken(): Promise<string | null> {
    if (!this.getAccessToken()) return null;
    if (!this.isAccessTokenExpired()) return this.getAccessToken();
    if (!this.getRefreshToken()) {
      this.logout();
      return null;
    }
    try {
      return await firstValueFrom(this.refreshAccessToken());
    } catch {
      return null;
    }
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
          this.scheduleCloudInferenceAuthSync();
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

  /** Push session JWT to the desktop runtime for Babo Cloud inference relay. */
  private scheduleCloudInferenceAuthSync(): void {
    queueMicrotask(() => {
      try {
        void import('./babo-cloud-provision.service').then(({ BaboCloudProvisionService }) => {
          const svc = this.injector.get(BaboCloudProvisionService);
          svc.invalidateSyncCache();
          void svc.syncRuntimeAuth();
        });
      } catch {
        /* web / bootstrap */
      }
    });
  }

  private scheduleCloudInferenceAuthClear(): void {
    queueMicrotask(() => {
      try {
        void import('./babo-cloud-provision.service').then(({ BaboCloudProvisionService }) => {
          this.injector.get(BaboCloudProvisionService).invalidateSyncCache();
        });
      } catch {
        /* web */
      }
    });
  }
}
