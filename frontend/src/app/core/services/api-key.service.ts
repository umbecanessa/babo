import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../../environments/environment';
import { ApiKey } from '../models/user.model';

@Injectable({ providedIn: 'root' })
export class ApiKeyService {
  private readonly API = environment.apiUrl;

  constructor(private http: HttpClient) {}

  getKeys(): Observable<ApiKey[]> {
    return this.http.get<ApiKey[]>(`${this.API}/api-keys`);
  }

  createKey(name: string, rateLimitRpm?: number): Observable<ApiKey> {
    return this.http.post<ApiKey>(`${this.API}/api-keys`, { name, rateLimitRpm });
  }

  revokeKey(id: string): Observable<any> {
    return this.http.post(`${this.API}/api-keys/${id}/revoke`, {});
  }

  deleteKey(id: string): Observable<any> {
    return this.http.delete(`${this.API}/api-keys/${id}`);
  }
}
