import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { ApiService } from './api.service';
import { PlatformService } from './platform.service';

type AnalyticsProperties = Record<string, string | number | boolean | null>;

interface AnalyticsConfig {
  enabled: boolean;
}

interface PendingEvent {
  name: string;
  properties?: AnalyticsProperties;
}

const INSTALL_ID_KEY = 'babo_analytics_install_id';
const ATTRIBUTION_KEY = 'babo_analytics_attribution';
const ATTRIBUTION_REF_KEY = 'babo_attribution_ref';

@Injectable({ providedIn: 'root' })
export class AnalyticsService {
  private enabled: boolean | null = null;
  private configPromise: Promise<void> | null = null;
  private configBaseUrl: string | null = null;
  private readonly queue: PendingEvent[] = [];
  private flushing = false;
  private handoffClaimed = false;
  private attributionRef: string | null = null;

  constructor(
    private http: HttpClient,
    private api: ApiService,
    private platform: PlatformService,
  ) {}

  /** Fire-and-forget product event. No-op when backend flag is off or config unreachable. */
  track(name: string, properties: AnalyticsProperties = {}): void {
    void this.enqueue(name, properties);
  }

  /** Read whether the backend accepts analytics (after config fetch). */
  async isEnabled(): Promise<boolean> {
    await this.ensureConfig();
    return this.enabled === true;
  }

  /** Claim landing-page attribution (clipboard ref or launch flag). Call once at setup start. */
  async claimAttributionHandoff(): Promise<void> {
    if (this.handoffClaimed) return;
    await this.ensureConfig();
    if (!this.enabled) return;

    let ref = this.readStoredAttributionRef();
    const nls = (window as unknown as {
      nls?: {
        getLaunchAttributionRef?: () => Promise<string | null>;
        readClipboard?: () => Promise<string>;
      };
    }).nls;

    if (!ref && nls?.getLaunchAttributionRef) {
      try {
        ref = (await nls.getLaunchAttributionRef()) || null;
      } catch {
        /* ignore */
      }
    }

    if (!ref && nls?.readClipboard) {
      try {
        const clip = (await nls.readClipboard()).trim();
        const match = /^babo:ref:([a-f0-9]+)$/i.exec(clip);
        if (match) ref = match[1];
      } catch {
        /* ignore */
      }
    }

    if (!ref) return;

    try {
      await this.api.whenReady();
      const base = await this.resolveApiBase();
      const result = await firstValueFrom(
        this.http.post<{ ok: boolean; ref: string; properties: Record<string, unknown> }>(
          `${base}/analytics/handoff/${encodeURIComponent(ref)}/claim`,
          { installId: this.installId() },
        ),
      );
      if (!result.ok) return;

      this.handoffClaimed = true;
      this.attributionRef = result.ref;
      this.mergeAttribution(result.properties);
      try {
        localStorage.setItem(ATTRIBUTION_REF_KEY, result.ref);
      } catch {
        /* ignore */
      }
    } catch {
      /* ignore invalid or expired refs */
    }
  }

  getAttributionRef(): string | null {
    return this.attributionRef;
  }

  captureAttributionFromUrl(): void {
    try {
      const params = new URLSearchParams(window.location.search);
      const utmSource = params.get('utm_source');
      const utmMedium = params.get('utm_medium');
      const utmCampaign = params.get('utm_campaign');
      const utmContent = params.get('utm_content');
      if (!utmSource && !utmMedium && !utmCampaign && !utmContent) return;

      const current = this.readAttribution();
      localStorage.setItem(
        ATTRIBUTION_KEY,
        JSON.stringify({
          ...current,
          ...(utmSource ? { utm_source: utmSource } : {}),
          ...(utmMedium ? { utm_medium: utmMedium } : {}),
          ...(utmCampaign ? { utm_campaign: utmCampaign } : {}),
          ...(utmContent ? { utm_content: utmContent } : {}),
        }),
      );
    } catch {
      /* ignore */
    }
  }

  private async enqueue(name: string, properties: AnalyticsProperties): Promise<void> {
    await this.ensureConfig();
    if (!this.enabled) return;

    this.queue.push({
      name,
      properties: {
        ...this.readAttribution(),
        ...(this.attributionRef ? { attribution_ref: this.attributionRef } : {}),
        ...properties,
      },
    });
    void this.flush();
  }

  private async ensureConfig(): Promise<void> {
    await this.api.whenReady();
    const base = await this.resolveApiBase();
    if (this.enabled !== null && this.configBaseUrl === base) return;
    if (this.configPromise && this.configBaseUrl === base) {
      await this.configPromise;
      return;
    }

    this.configBaseUrl = base;
    this.enabled = null;
    this.configPromise = this.loadConfig(base);
    await this.configPromise;
  }

  private async loadConfig(base: string): Promise<void> {
    try {
      const config = await firstValueFrom(
        this.http.get<AnalyticsConfig>(`${base}/analytics/config`),
      );
      this.enabled = !!config.enabled;
    } catch {
      this.enabled = false;
    }
  }

  private async resolveApiBase(): Promise<string> {
    const nls = (window as unknown as {
      nls?: { config?: { get: () => Promise<{ nestjsUrl?: string }> } };
    }).nls;
    try {
      const cfg = await nls?.config?.get?.();
      if (cfg?.nestjsUrl) {
        return ApiService.nestjsApiBase(cfg.nestjsUrl);
      }
    } catch {
      /* use ApiService default */
    }
    return this.api.getApiBaseUrl();
  }

  private async flush(): Promise<void> {
    if (this.flushing || !this.enabled || this.queue.length === 0) return;
    this.flushing = true;
    const batch = this.queue.splice(0, 20);
    try {
      await this.api.whenReady();
      const base = await this.resolveApiBase();
      await firstValueFrom(
        this.http.post(`${base}/analytics/events`, {
          events: batch.map((event) => ({
            name: event.name,
            installId: this.installId(),
            platform: this.platform.isElectron ? 'desktop' : 'web',
            appVersion: this.appVersion(),
            occurredAt: new Date().toISOString(),
            properties: event.properties ?? {},
          })),
        }),
      );
    } catch {
      this.queue.unshift(...batch);
    } finally {
      this.flushing = false;
      if (this.queue.length > 0) {
        void this.flush();
      }
    }
  }

  private installId(): string {
    try {
      let id = localStorage.getItem(INSTALL_ID_KEY);
      if (!id) {
        id = crypto.randomUUID();
        localStorage.setItem(INSTALL_ID_KEY, id);
      }
      return id;
    } catch {
      return 'unknown';
    }
  }

  private readAttribution(): AnalyticsProperties {
    try {
      const raw = localStorage.getItem(ATTRIBUTION_KEY);
      if (!raw) return {};
      const parsed = JSON.parse(raw) as AnalyticsProperties;
      return parsed && typeof parsed === 'object' ? parsed : {};
    } catch {
      return {};
    }
  }

  private readStoredAttributionRef(): string | null {
    try {
      return localStorage.getItem(ATTRIBUTION_REF_KEY);
    } catch {
      return null;
    }
  }

  private mergeAttribution(properties: Record<string, unknown>): void {
    const safe: AnalyticsProperties = {};
    for (const [key, value] of Object.entries(properties)) {
      if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
        safe[key] = value;
      }
    }
    try {
      localStorage.setItem(
        ATTRIBUTION_KEY,
        JSON.stringify({ ...this.readAttribution(), ...safe }),
      );
    } catch {
      /* ignore */
    }
  }

  private appVersion(): string | undefined {
    const nls = (window as any).nls;
    const version = nls?.version ?? nls?.appVersion;
    return typeof version === 'string' ? version : undefined;
  }
}
