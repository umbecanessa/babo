import { Injectable } from '@angular/core';
import { firstValueFrom } from 'rxjs';
import { ApiKeyService } from './api-key.service';
import { PlatformService } from './platform.service';
import { AuthService } from './auth.service';
import { ApiService } from './api.service';
import { usesBaboCloudRelay } from '../../features/setup/setup-cloud.util';
import { isLikelySessionJwt } from '../../features/setup/setup-inference.util';
import { isHybridLocalInferenceTier } from './model-catalog.util';
import type { CapabilityProfile } from '../../features/setup/capability-profile.model';

/**
 * Sync Babo Cloud inference auth to the desktop Python runtime.
 *
 * Nest accepts Bearer JWT (signed-in users) or ``nlsk_`` API keys. The Angular
 * UI uses JWT via the interceptor; the runtime only sees ``NLS_INFERENCE_API_KEY``,
 * so we push the resolved bearer here on boot, login, token refresh, and runtime ready.
 */
@Injectable({ providedIn: 'root' })
export class BaboCloudProvisionService {
  private syncInFlight: Promise<string | null> | null = null;
  private lastSyncedBearer: string | null = null;

  constructor(
    private apiKeys: ApiKeyService,
    private platform: PlatformService,
    private auth: AuthService,
    private api: ApiService,
  ) {}

  /** Push JWT or configured API key to the local runtime (idempotent). */
  syncRuntimeAuth(): Promise<string | null> {
    if (!this.syncInFlight) {
      this.syncInFlight = this.doSyncRuntimeAuth().finally(() => {
        this.syncInFlight = null;
      });
    }
    return this.syncInFlight;
  }

  /** Legacy alias — prefer {@link syncRuntimeAuth}. */
  ensureInferenceApiKey(): Promise<string | null> {
    return this.syncRuntimeAuth();
  }

  /** Drop cached bearer so the next sync always hot-reloads (login / refresh). */
  invalidateSyncCache(): void {
    this.lastSyncedBearer = null;
  }

  /** Optional per-agent ``nlsk_`` key (automation / long-lived sessions). */
  async ensureAgentScopedApiKey(opts: {
    agentId?: string;
    agentName: string;
  }): Promise<string | null> {
    if (!this.platform.isElectron || !this.auth.isAuthenticated()) return null;
    await this.api.whenReady();

    const nls = (window as any).nls;
    if (!nls?.config?.get) return null;

    const cfg = await nls.config.get();
    const existing = (cfg.inferenceApiKey || '').trim();
    if (existing.startsWith('nlsk_')) {
      this.invalidateSyncCache();
      await this.hotReloadRuntime(existing);
      this.lastSyncedBearer = existing;
      return existing;
    }

    try {
      const created = await firstValueFrom(
        this.apiKeys.createKey(opts.agentName, {
          agentId: opts.agentId,
          rateLimitRpm: 120,
          scopes: ['inference', 'gpu'],
        }),
      );
      if (!created.key) {
        return this.syncRuntimeAuth();
      }
      await nls.config.set({ inferenceApiKey: created.key });
      this.invalidateSyncCache();
      await this.hotReloadRuntime(created.key);
      this.lastSyncedBearer = created.key;
      return created.key;
    } catch {
      return this.syncRuntimeAuth();
    }
  }

  private async doSyncRuntimeAuth(): Promise<string | null> {
    if (!this.platform.isElectron) return null;

    await this.api.whenReady();

    const bearer = await this.resolveInferenceBearer();
    if (!bearer) return null;

    if (bearer === this.lastSyncedBearer) return bearer;

    await this.hotReloadRuntime(bearer);
    this.lastSyncedBearer = bearer;
    return bearer;
  }

  /** Bearer for cloud relay: explicit ``nlsk_`` / BYOK key, else session JWT. */
  private async resolveInferenceBearer(): Promise<string | null> {
    const nls = (window as any).nls;
    if (!nls?.config?.get) return null;

    const cfg = await nls.config.get();
    const profile = cfg.capabilityProfile as CapabilityProfile | undefined;
    const stored = (cfg.inferenceApiKey || '').trim();
    const tier = profile?.inference?.tier ?? 'hosted_babo';
    const hybridLocal = isHybridLocalInferenceTier(tier);
    const relay =
      usesBaboCloudRelay(profile ?? null) ||
      (hybridLocal && !!String(cfg.nestjsUrl || '').trim()) ||
      (!hybridLocal &&
        /\/api\/inference/i.test(String(cfg.inferenceUrl || '')));

    if (!relay) {
      if (stored && isLikelySessionJwt(stored)) return null;
      return stored || null;
    }

    if (stored.startsWith('nlsk_')) {
      return stored;
    }

    if (profile?.inference?.tier === 'byok_cloud' && stored) {
      return stored;
    }

    const jwt = await this.auth.ensureFreshAccessToken();
    if (jwt) {
      return jwt;
    }

    return stored && !isLikelySessionJwt(stored) ? stored : null;
  }

  private async hotReloadRuntime(bearer: string): Promise<void> {
    const nls = (window as any).nls;
    if (!nls?.runtime?.hotReloadInference) return;
    try {
      await nls.runtime.hotReloadInference({ inference_api_key: bearer });
    } catch {
      /* runtime may not be up yet — caller should retry after markRuntimeReady */
    }
  }
}
