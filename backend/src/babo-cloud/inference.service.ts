import {
  HttpException,
  HttpStatus,
  Injectable,
  Logger,
} from '@nestjs/common';
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

@Injectable()
export class InferenceService {
  private readonly logger = new Logger(InferenceService.name);
  private readonly defaultRpm: number;
  /** Short-lived cache of the live GX10 model id for ``babo-hosted``. */
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

  /** Live model ids from GX10 ``GET /v1/models`` (excludes the babo-hosted alias). */
  private async probeHostedUpstreamModelIds(
    upstream: ResolvedInferenceUpstream,
  ): Promise<string[]> {
    const url = `${upstream.baseUrl.replace(/\/+$/, '')}/models`;
    const res = await fetch(url, {
      headers: this.upstreamHeaders(upstream.apiKey),
    });
    if (!res.ok) {
      throw new Error(`GET /models HTTP ${res.status}`);
    }
    const data = (await res.json()) as {
      data?: Array<{ id?: string }>;
    };
    return (data.data ?? [])
      .map((row) => row?.id?.trim())
      .filter((id): id is string => !!id && id.toLowerCase() !== 'babo-hosted');
  }

  /**
   * Map desktop alias ``babo-hosted`` to whatever model GX10 is serving now.
   * Always prefers a live ``/v1/models`` poll (TTL cache). Static
   * ``INFERENCE_UPSTREAM_MODEL`` is fallback only when the probe fails —
   * GX10 can only run one large model at a time, so the catalog is the source
   * of truth when swapping teacher ↔ flash.
   */
  private async resolveHostedUpstreamModel(
    upstream: ResolvedInferenceUpstream,
    requestedModel: string,
    options?: { forceRefresh?: boolean },
  ): Promise<string> {
    if (upstream.placement !== 'hosted_babo') return requestedModel;
    if (requestedModel.toLowerCase() !== 'babo-hosted') return requestedModel;

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
      const ids = await this.probeHostedUpstreamModelIds(upstream);
      if (ids.length) {
        // Prefer previous id if still listed (avoids thrashing mid-request);
        // otherwise take the first live model — typically the only one.
        const next =
          (this.hostedUpstreamModelCache &&
            ids.includes(this.hostedUpstreamModelCache) &&
            this.hostedUpstreamModelCache) ||
          ids[0];
        if (next !== this.hostedUpstreamModelCache) {
          this.logger.log(`GX10 upstream model for babo-hosted: ${next}`);
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

  async listModels(auth: CloudAuthContext): Promise<unknown> {
    await this.assertRateLimit(auth);
    const upstream = await this.providerKeys.resolveInferenceUpstream(
      auth.userId,
    );
    await this.entitlements.assertCloudAccessForPlacement(
      auth.userId,
      upstream.placement,
    );
    const url = `${upstream.baseUrl.replace(/\/+$/, '')}/models`;
    const res = await fetch(url, {
      headers: this.upstreamHeaders(upstream.apiKey),
    });
    const text = await res.text();
    if (!res.ok) {
      throw new HttpException(text || res.statusText, res.status);
    }
    const data = JSON.parse(text) as { data?: Array<Record<string, unknown>> };
    const gx10Enabled = await this.entitlements.getHostedGx10Enabled(
      auth.userId,
    );
    if (gx10Enabled && this.config.get<string>('INFERENCE_UPSTREAM_URL')) {
      const rows = Array.isArray(data.data) ? data.data : [];
      // Refresh hosted alias cache from the live catalog while we're here.
      const liveIds = rows
        .map((row) => String(row?.id || '').trim())
        .filter((id) => id && id.toLowerCase() !== 'babo-hosted');
      if (liveIds.length) {
        this.hostedUpstreamModelCache = liveIds[0];
        this.hostedUpstreamModelCacheAt = Date.now();
      }
      if (!rows.some((row) => row?.id === 'babo-hosted')) {
        rows.unshift({
          id: 'babo-hosted',
          object: 'model',
          owned_by: 'babo',
        });
      }
      return { ...data, data: rows };
    }
    return data;
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
    let upstreamModel = await this.resolveHostedUpstreamModel(upstream, model);
    let payload =
      upstreamModel !== model ? { ...body, model: upstreamModel } : body;
    const url = `${upstream.baseUrl.replace(/\/+$/, '')}/chat/completions`;

    let upstreamRes = await fetch(url, {
      method: 'POST',
      headers: this.upstreamHeaders(upstream.apiKey),
      body: JSON.stringify(payload),
    });

    // Model swap on GX10 (teacher → flash): stale cache / env override → 404.
    // Re-probe once and retry with the live id.
    if (
      upstreamRes.status === 404 &&
      upstream.placement === 'hosted_babo' &&
      model.toLowerCase() === 'babo-hosted'
    ) {
      this.clearHostedUpstreamModelCache();
      const refreshed = await this.resolveHostedUpstreamModel(
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
            model: json.model || model,
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
