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

export interface AdminPlatformInfo {
  baboCloudMode: boolean;
  billingEnabled: boolean;
  billingProvider: string;
}

export interface FunnelOverview {
  enabled: boolean;
  periodDays: number;
  message?: string;
  web: {
    pageViews: number;
    uniqueVisitors: number;
    ctaClicks: number;
    outboundClicks: number;
    events: { name: string; source: string; count: number; uniqueVisitors: number }[];
    ctaByLocation: { location: string; count: number; uniqueVisitors: number }[];
    audiences: { audience: string; uniqueVisitors: number }[];
    campaigns: {
      campaign: string;
      source: string;
      pageViews: number;
      uniqueVisitors: number;
    }[];
  } | null;
  app: {
    setupStarted: number;
    setupCompleted: number;
    setupAbandoned: number;
    billingActivated: number;
    events: { name: string; source: string; count: number; uniqueVisitors: number }[];
    steps: {
      step: string;
      views: number;
      uniqueInstalls: number;
      dropOffFromPrevious: number | null;
    }[];
    completionRate: number | null;
  } | null;
  attribution: {
    handoffsCreated: number;
    handoffsClaimed: number;
    claimToSetupStarted: number;
    claimToCompleted: number;
    byCampaign: {
      campaign: string;
      handoffs: number;
      claimed: number;
      completed: number;
    }[];
  } | null;
}

@Injectable({ providedIn: 'root' })
export class AdminApiService {
  private base = `${environment.apiUrl}/admin`;
  private platformCache: AdminPlatformInfo | null = null;

  constructor(private http: HttpClient) {}

  platform(force = false) {
    if (this.platformCache && !force) {
      return Promise.resolve(this.platformCache);
    }
    return firstValueFrom(
      this.http.get<AdminPlatformInfo>(`${this.base}/platform`),
    ).then((p) => {
      this.platformCache = p;
      return p;
    });
  }

  setupStatus() {
    return firstValueFrom(
      this.http.get<SetupStatus>(`${this.base}/setup/status`),
    );
  }

  dashboard() {
    return firstValueFrom(this.http.get<any>(`${this.base}/dashboard`));
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

  agentDb(id: string) {
    return firstValueFrom(this.http.get<any>(`${this.base}/agents/db/${id}`));
  }

  agentInspect(id: string) {
    return firstValueFrom(this.http.get<any>(`${this.base}/agents/db/${id}/inspect`));
  }

  deleteAgentDb(id: string) {
    return firstValueFrom(this.http.delete(`${this.base}/agents/db/${id}`));
  }

  evictAgent(runtimeId: string) {
    return firstValueFrom(
      this.http.post(`${this.base}/agents/${runtimeId}/evict`, {}),
    );
  }

  sleepAgent(runtimeId: string) {
    return firstValueFrom(
      this.http.post(`${this.base}/agents/${runtimeId}/sleep`, {}),
    );
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

  billingSubscriptions() {
    return firstValueFrom(
      this.http.get<any>(`${this.base}/billing/subscriptions`),
    );
  }

  funnel(days = 30) {
    return firstValueFrom(
      this.http.get<FunnelOverview>(`${this.base}/analytics/funnel`, {
        params: { days: String(days) },
      }),
    );
  }

  grantLifetime(userId: string, grantNote?: string) {
    return firstValueFrom(
      this.http.post<any>(`${this.base}/users/${userId}/grant-lifetime`, {
        grantNote: grantNote || undefined,
      }),
    );
  }

  revokeLifetime(userId: string) {
    return firstValueFrom(
      this.http.post<any>(`${this.base}/users/${userId}/revoke-lifetime`, {}),
    );
  }

  activateCloudBasicDev(userId: string) {
    return firstValueFrom(
      this.http.post<any>(`${this.base}/users/${userId}/activate-cloud-basic`, {}),
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
