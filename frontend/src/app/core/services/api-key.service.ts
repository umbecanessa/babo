import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { ApiKey } from '../models/user.model';
import { ApiService } from './api.service';

@Injectable({ providedIn: 'root' })
export class ApiKeyService {
  constructor(
    private http: HttpClient,
    private api: ApiService,
  ) {}

  private get API(): string {
    return this.api.apiBase;
  }

  getKeys(): Observable<ApiKey[]> {
    return this.http.get<ApiKey[]>(`${this.API}/api-keys`);
  }

  createKey(
    name: string,
    opts?: { rateLimitRpm?: number; agentId?: string; scopes?: string[] },
  ): Observable<ApiKey & { key?: string }> {
    return this.http.post<ApiKey & { key?: string }>(`${this.API}/api-keys`, {
      name,
      rateLimitRpm: opts?.rateLimitRpm,
      agentId: opts?.agentId,
      scopes: opts?.scopes ?? ['inference', 'gpu'],
    });
  }

  revokeKey(id: string): Observable<any> {
    return this.http.post(`${this.API}/api-keys/${id}/revoke`, {});
  }

  deleteKey(id: string): Observable<any> {
    return this.http.delete(`${this.API}/api-keys/${id}`);
  }
}
