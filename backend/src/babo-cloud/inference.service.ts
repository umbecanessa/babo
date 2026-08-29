import { HttpException, Injectable, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { randomUUID } from 'crypto';
import type { Response } from 'express';
import { ApiKeysService } from '../api-keys/api-keys.service';
import { CloudAuthContext } from './cloud-auth.types';
import { CloudRateLimiterService } from './cloud-rate-limiter.service';
import { CloudUsageService } from './cloud-usage.service';
import { EntitlementsService } from './entitlements.service';
import { CloudUpstreamService } from './cloud-upstream.service';
import { ProviderKeysService } from './provider-keys.service';
import type { ResolvedInferenceUpstream } from './provider-keys.service';
import {
  BABO_BRAIN_LABEL,
  BABO_BRAIN_MODEL_ID,
  isBaboBrainModelId,
} from './babo-brain.constants';

@Injectable()
export class InferenceService {
  private readonly logger = new Logger(InferenceService.name);
  private readonly defaultRpm: number;
  /** Short-lived cache of the live GX10 served model id behind Babo Brain. */
  private hostedUpstreamModelCache: string | null = null;
  private hostedUpstreamModelCacheAt = 0;
  /** Re-probe at least this often so swapping the only GX10 model picks up. */
  private static readonly HOSTED_MODEL_CACHE_TTL_MS = 60_000;

  constructor(
    private config: ConfigService,
    private apiKeys: ApiKeysService,
    private rateLimiter: CloudRateLimiterService,
    private usage: CloudUsageService,
    private providerKeys: ProviderKeysService,
    private entitlements: EntitlementsService,
    private upstream: CloudUpstreamService,
  ) {
    this.defaultRpm = Number(this.config.get('INFERENCE_DEFAULT_RPM') || 120);
  }

  private async assertRateLimit(auth: CloudAuthContext): Promise<void> {
    let rpm = this.defaultRpm;
    if (auth.apiKeyId) {
      const keyRpm = await this.apiKeys.getRateLimitRpm(auth.apiKeyId);
      if (keyRpm != null) rpm = keyRpm;
    }
    this.rateLimiter.assertWithinLimit(auth, rpm);
  }

  private upstreamHeaders(apiKey: string): Record<string, string> {
    const h: Record<string, string> = { 'Content-Type': 'application/json' };
    if (apiKey) h.Authorization = `Bearer ${apiKey}`;
    return h;
  }

  private clearHostedUpstreamModelCache(): void {
    this.hostedUpstreamModelCache = null;
    this.hostedUpstreamModelCacheAt = 0;
  }

  private baboBrainCatalogRow(): Record<string, unknown> {
    return {
      id: BABO_BRAIN_MODEL_ID,
      object: 'model',
      owned_by: 'babo',
      name: BABO_BRAIN_LABEL,
    };
  }

  /** Live served ids from GX10 ``GET /v1/models`` (never the product id). */
  private async probeGx10ServedModelIds(): Promise<string[]> {
    if (!this.upstream.isInferenceConfigured()) return [];
    const url = `${this.upstream.inferenceApiBase().replace(/\/+$/, '')}/models`;
    const res = await fetch(url, {
      headers: this.upstreamHeaders(this.upstream.inferenceUpstreamKey),
    });
    if (!res.ok) {
      throw new Error(`GET /models HTTP ${res.status}`);
    }
    const data = (await res.json()) as {
      data?: Array<{ id?: string }>;
    };
    return (data.data ?? [])
      .map((row) => row?.id?.trim())
      .filter(
        (id): id is string => !!id && !isBaboBrainModelId(id),
      );
  }

  /**
   * Babo Brain → whatever single model GX10 is serving right now.
   * OpenRouter / BYOK model ids are never rewritten.
   */
  private async resolveBaboBrainUpstreamModel(
    upstream: ResolvedInferenceUpstream,
    requestedModel: string,
    options?: { forceRefresh?: boolean },
  ): Promise<string> {
    if (upstream.placement !== 'hosted_babo') return requestedModel;
    if (!isBaboBrainModelId(requestedModel)) return requestedModel;

    const now = Date.now();
    const cacheFresh =
      !options?.forceRefresh &&
      !!this.hostedUpstreamModelCache &&
      now - this.hostedUpstreamModelCacheAt <
        InferenceService.HOSTED_MODEL_CACHE_TTL_MS;

    if (cacheFresh) {
      return this.hostedUpstreamModelCache as string;
    }

    try {
      const ids = await this.probeGx10ServedModelIds();
      if (ids.length) {
        const next =
          (this.hostedUpstreamModelCache &&
            ids.includes(this.hostedUpstreamModelCache) &&
            this.hostedUpstreamModelCache) ||
          ids[0];
        if (next !== this.hostedUpstreamModelCache) {
          this.logger.log(`Babo Brain → GX10 served model: ${next}`);
        }
        this.hostedUpstreamModelCache = next;
        this.hostedUpstreamModelCacheAt = now;
        return next;
      }
    } catch (err: any) {
      this.logger.warn(`GX10 model catalog probe failed: ${err.message}`);
    }

    if (this.upstream.inferenceUpstreamModel) {
      this.logger.warn(
        `GX10 /v1/models empty; falling back to INFERENCE_UPSTREAM_MODEL=` +
          this.upstream.inferenceUpstreamModel,
      );
      return this.upstream.inferenceUpstreamModel;
    }

    if (this.hostedUpstreamModelCache) {
      return this.hostedUpstreamModelCache;
    }

    return requestedModel;
  }

  private async refreshBaboBrainUpstreamCache(): Promise<void> {
    try {
      const ids = await this.probeGx10ServedModelIds();
      if (ids.length) {
        this.hostedUpstreamModelCache = ids[0];
        this.hostedUpstreamModelCacheAt = Date.now();
      }
    } catch (err: any) {
      this.logger.debug(`GX10 catalog refresh skipped: ${err.message}`);
    }
  }

  async listModels(auth: CloudAuthContext): Promise<unknown> {
    await this.assertRateLimit(auth);
    const upstream = await this.providerKeys.resolveInferenceUpstream(
      auth.userId,
    );
    await this.entitlements.assertCloudAccessForPlacement(
      auth.userId,
      upstream.placement,
    );

    const gx10Enabled = await this.entitlements.getHostedGx10Enabled(
      auth.userId,
    );
    const gx10Configured = this.upstream.isInferenceConfigured();

    // Pure GX10 placement: expose only Babo Brain — never the raw served id.
    if (upstream.placement === 'hosted_babo' && gx10Configured) {
      void this.refreshBaboBrainUpstreamCache();
      return {
        object: 'list',
        data: [this.baboBrainCatalogRow()],
      };
    }

    const url = `${upstream.baseUrl.replace(/\/+$/, '')}/models`;
    const res = await fetch(url, {
      headers: this.upstreamHeaders(upstream.apiKey),
    });
    const text = await res.text();
    if (!res.ok) {
      throw new HttpException(text || res.statusText, res.status);
    }
    const data = JSON.parse(text) as { data?: Array<Record<string, unknown>> };
    const rows = Array.isArray(data.data) ? [...data.data] : [];

    // Lifetime / GX10 entitlement on a resold catalog: keep OpenRouter names,
    // add a single Babo Brain product entry (no GX10 served-id aliases).
    if (gx10Enabled && gx10Configured) {
      void this.refreshBaboBrainUpstreamCache();
      const withoutBrain = rows.filter(
        (row) => !isBaboBrainModelId(String(row?.id || '')),
      );
      return {
        ...data,
        data: [this.baboBrainCatalogRow(), ...withoutBrain],
      };
    }

    return { ...data, data: rows };
  }

  async proxyChatCompletions(
    auth: CloudAuthContext,
    body: Record<string, unknown>,
    res: Response,
  ): Promise<void> {
    await this.assertRateLimit(auth);

    const stream = body.stream === true;
    const model = String(body.model || 'unknown');
    const requestId = randomUUID();
    const route = 'chat/completions';
    const upstream = await this.providerKeys.resolveInferenceUpstream(
      auth.userId,
      model,
    );
    await this.entitlements.assertCloudAccessForPlacement(
      auth.userId,
      upstream.placement,
    );
    let upstreamModel = await this.resolveBaboBrainUpstreamModel(
      upstream,
      model,
    );
    let payload =
      upstreamModel !== model ? { ...body, model: upstreamModel } : body;
    const url = `${upstream.baseUrl.replace(/\/+$/, '')}/chat/completions`;

    let upstreamRes = await fetch(url, {
      method: 'POST',
      headers: this.upstreamHeaders(upstream.apiKey),
      body: JSON.stringify(payload),
    });

    // Model swap on GX10: stale cache → 404. Re-probe once and retry.
    if (
      upstreamRes.status === 404 &&
      upstream.placement === 'hosted_babo' &&
      isBaboBrainModelId(model)
    ) {
      this.clearHostedUpstreamModelCache();
      const refreshed = await this.resolveBaboBrainUpstreamModel(
        upstream,
        model,
        { forceRefresh: true },
      );
      if (refreshed && refreshed !== upstreamModel) {
        this.logger.warn(
          `GX10 model 404 for ${upstreamModel}; retrying as ${refreshed}`,
        );
        upstreamModel = refreshed;
        payload = { ...body, model: refreshed };
        upstreamRes = await fetch(url, {
          method: 'POST',
          headers: this.upstreamHeaders(upstream.apiKey),
          body: JSON.stringify(payload),
        });
      }
    }

    if (!stream) {
      const text = await upstreamRes.text();
      if (!upstreamRes.ok) {
        res.status(upstreamRes.status).send(text);
        return;
      }
      const data = JSON.parse(text);
      if (data?.usage) {
        await this.usage.record({
          auth,
          requestId,
          workload: 'inference',
          placement: upstream.placement,
          model,
          route,
          provider: upstream.provider,
          usage: this.usage.normalizeUsage(data.usage),
        });
      }
      // Always report the product id to clients, not the served GX10 name.
      if (isBaboBrainModelId(model) && data && typeof data === 'object') {
        data.model = BABO_BRAIN_MODEL_ID;
      }
      res.status(upstreamRes.status).json(data);
      return;
    }

    if (!upstreamRes.ok) {
      const errText = await upstreamRes.text();
      res.status(upstreamRes.status).send(errText);
      return;
    }

    res.status(upstreamRes.status);
    res.setHeader(
      'Content-Type',
      upstreamRes.headers.get('content-type') || 'text/event-stream',
    );
    res.setHeader('Cache-Control', 'no-cache');
    res.setHeader('Connection', 'keep-alive');

    if (!upstreamRes.body) {
      res.end();
      return;
    }

    const reader = upstreamRes.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const chunk = decoder.decode(value, { stream: true });
        buffer += chunk;
        res.write(chunk);
        buffer = await this.consumeSseForUsage(
          auth,
          requestId,
          model,
          route,
          upstream,
          buffer,
        );
      }
    } catch (err: any) {
      this.logger.warn(`Stream relay error: ${err.message}`);
    } finally {
      await this.consumeSseForUsage(
        auth,
        requestId,
        model,
        route,
        upstream,
        buffer,
        true,
      );
      res.end();
    }
  }

  private async consumeSseForUsage(
    auth: CloudAuthContext,
    requestId: string,
    model: string,
    route: string,
    upstream: { placement: string; provider: string },
    buffer: string,
    flushAll = false,
  ): Promise<string> {
    const lines = buffer.split('\n');
    const tail = flushAll ? '' : lines.pop() ?? '';

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed.startsWith('data:')) continue;
      const payload = trimmed.slice(5).trim();
      if (!payload || payload === '[DONE]') continue;
      try {
        const json = JSON.parse(payload);
        if (json.usage) {
          await this.usage.record({
            auth,
            requestId,
            workload: 'inference',
            placement: upstream.placement,
            // Bill / attribute as Babo Brain, not the served flash/teacher id.
            model: isBaboBrainModelId(model)
              ? BABO_BRAIN_MODEL_ID
              : json.model || model,
            route,
            provider: upstream.provider,
            usage: this.usage.normalizeUsage(json.usage),
          });
        }
      } catch {
        /* partial line */
      }
    }
    return tail;
  }
}
