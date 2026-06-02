import { Injectable, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../environments/environment';
import { jwtRole } from './jwt.util';

export interface AuthTokens {
  accessToken: string;
  refreshToken: string;
  userId: string;
  role: string;
  email?: string;
}

const STORAGE_KEY = 'babo_admin_auth';

@Injectable({ providedIn: 'root' })
export class AuthService {
  readonly session = signal<AuthTokens | null>(this.loadValid());

  constructor(private http: HttpClient) {}

  get isAdmin(): boolean {
    return this.session()?.role === 'admin';
  }

  get accessToken(): string | null {
    return this.session()?.accessToken ?? null;
  }

  get operatorEmail(): string | null {
    return this.session()?.email ?? null;
  }

  private loadValid(): AuthTokens | null {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return null;
      const parsed = JSON.parse(raw) as AuthTokens;
      if (!parsed.accessToken || parsed.role !== 'admin') {
        localStorage.removeItem(STORAGE_KEY);
        return null;
      }
      const role = jwtRole(parsed.accessToken);
      if (role && role !== 'admin') {
        localStorage.removeItem(STORAGE_KEY);
        return null;
      }
      if (!parsed.role && role === 'admin') {
        parsed.role = 'admin';
      }
      return parsed;
    } catch {
      return null;
    }
  }

  private persist(tokens: AuthTokens): void {
    if (tokens.role !== 'admin') {
      throw new Error('Not an administrator session');
    }
    localStorage.setItem(STORAGE_KEY, JSON.stringify(tokens));
    this.session.set(tokens);
  }

  logout(): void {
    localStorage.removeItem(STORAGE_KEY);
    this.session.set(null);
  }

  async login(email: string, password: string): Promise<AuthTokens> {
    const res = await firstValueFrom(
      this.http.post<AuthTokens>(`${environment.apiUrl}/auth/admin/login`, {
        email,
        password,
      }),
    );
    const emailFromJwt = this.emailFromToken(res.accessToken) ?? email;
    const tokens: AuthTokens = { ...res, role: res.role ?? 'admin', email: emailFromJwt };
    if (tokens.role !== 'admin') {
      throw new Error('Administrator access only');
    }
    this.persist(tokens);
    return tokens;
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
      if (res.role !== 'admin') {
        this.logout();
        return;
      }
      this.persist({ ...res, email: s.email ?? this.emailFromToken(res.accessToken) ?? undefined });
    } catch {
      this.logout();
    }
  }

  private emailFromToken(token: string): string | null {
    try {
      const part = token.split('.')[1];
      const json = JSON.parse(atob(part.replace(/-/g, '+').replace(/_/g, '/')));
      return typeof json.email === 'string' ? json.email : null;
    } catch {
      return null;
    }
  }
}
