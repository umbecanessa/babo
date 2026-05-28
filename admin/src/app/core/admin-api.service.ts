import { Injectable } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../environments/environment';

export interface SetupStatus {
  needsSetup: boolean;
  hasAdmin: boolean;
  userCount: number;
  canClaimExisting: boolean;
  setupMode: 'create' | 'claim' | 'ready';
}

@Injectable({ providedIn: 'root' })
export class AdminApiService {
  private base = `${environment.apiUrl}/admin`;

  constructor(private http: HttpClient) {}

  setupStatus() {
    return firstValueFrom(
      this.http.get<SetupStatus>(`${this.base}/setup/status`),
    );
  }

  stats() {
    return firstValueFrom(this.http.get<any>(`${this.base}/stats`));
  }

  users() {
    return firstValueFrom(this.http.get<any[]>(`${this.base}/users`));
  }

  user(id: string) {
    return firstValueFrom(this.http.get<any>(`${this.base}/users/${id}`));
  }

  updateRole(id: string, role: 'user' | 'admin') {
    return firstValueFrom(
      this.http.patch(`${this.base}/users/${id}/role`, { role }),
    );
  }

  deleteUser(id: string) {
    return firstValueFrom(this.http.delete(`${this.base}/users/${id}`));
  }

  agents() {
    return firstValueFrom(this.http.get<any[]>(`${this.base}/agents`));
  }

  usage(limit = 50) {
    const params = new HttpParams().set('limit', String(limit));
    return firstValueFrom(this.http.get<any>(`${this.base}/usage`, { params }));
  }

  userUsage(userId: string, limit = 50) {
    const params = new HttpParams().set('limit', String(limit));
    return firstValueFrom(
      this.http.get<any>(`${this.base}/users/${userId}/usage`, { params }),
    );
  }

  static errorMessage(err: unknown): string {
    const e = err as { error?: { message?: string | string[] }; message?: string; status?: number };
    const msg = e?.error?.message;
    if (Array.isArray(msg)) return msg.join(', ');
    if (typeof msg === 'string') return msg;
    if (e?.status === 403) return 'Administrator access required.';
    if (e?.status === 401) return 'Session expired or invalid credentials.';
    return e?.message || 'Request failed';
  }
}
