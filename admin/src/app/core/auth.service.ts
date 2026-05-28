import { Injectable, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../environments/environment';

export interface AuthTokens {
  accessToken: string;
  refreshToken: string;
  userId: string;
  role: string;
}

const STORAGE_KEY = 'babo_admin_auth';

@Injectable({ providedIn: 'root' })
export class AuthService {
  readonly session = signal<AuthTokens | null>(this.load());

  constructor(private http: HttpClient) {}

  get isAdmin(): boolean {
    return this.session()?.role === 'admin';
  }

  get accessToken(): string | null {
    return this.session()?.accessToken ?? null;
  }

  private load(): AuthTokens | null {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch {
      return null;
    }
  }

  private persist(tokens: AuthTokens): void {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(tokens));
    this.session.set(tokens);
  }

  logout(): void {
    localStorage.removeItem(STORAGE_KEY);
    this.session.set(null);
  }

  async login(email: string, password: string): Promise<AuthTokens> {
    const res = await firstValueFrom(
      this.http.post<AuthTokens>(`${environment.apiUrl}/auth/login`, { email, password }),
    );
    this.persist(res);
    return res;
  }

  async bootstrap(email: string, password: string, displayName?: string): Promise<AuthTokens> {
    await firstValueFrom(
      this.http.post(`${environment.apiUrl}/admin/setup`, { email, password, displayName }),
    );
    return this.login(email, password);
  }

  async refreshIfNeeded(): Promise<void> {
    const s = this.session();
    if (!s?.refreshToken) return;
    try {
      const res = await firstValueFrom(
        this.http.post<AuthTokens>(`${environment.apiUrl}/auth/refresh`, {
          refreshToken: s.refreshToken,
        }),
      );
      this.persist(res);
    } catch {
      this.logout();
    }
  }
}
