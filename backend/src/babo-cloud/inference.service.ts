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
  /** Cached first model id from GX10 /v1/models (for ``babo-hosted`` alias). */
  private hostedUpstreamModelCache: string | null = null;

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

  /** Map desktop alias ``babo-hosted`` to a real vLLM model id on GX10. */
  private async resolveHostedUpstreamModel(
    upstream: ResolvedInferenceUpstream,
    requestedModel: string,
  ): Promise<string> {
    if (upstream.placement !== 'hosted_babo') return requestedModel;
    if (requestedModel.toLowerCase() !== 'babo-hosted') return requestedModel;

    if (this.upstream.inferenceUpstreamModel) {
      return this.upstream.inferenceUpstreamModel;
    }

    if (this.hostedUpstreamModelCache) {
      return this.hostedUpstreamModelCache;
    }

    try {
      const url = `${upstream.baseUrl.replace(/\/+$/, '')}/models`;
      const res = await fetch(url, {
        headers: this.upstreamHeaders(upstream.apiKey),
      });
      if (res.ok) {
        const data = (await res.json()) as {
          data?: Array<{ id?: string }>;
        };
        const ids = (data.data ?? [])
          .map((row) => row?.id?.trim())
          .filter((id): id is string => !!id && id !== 'babo-hosted');
        if (ids.length) {
          this.hostedUpstreamModelCache = ids[0];
          this.logger.log(`GX10 upstream model for babo-hosted: ${ids[0]}`);
          return ids[0];
        }
      }
    } catch (err: any) {
      this.logger.warn(`GX10 model catalog probe failed: ${err.message}`);
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
    const upstreamModel = await this.resolveHostedUpstreamModel(upstream, model);
    const payload =
      upstreamModel !== model ? { ...body, model: upstreamModel } : body;
    const url = `${upstream.baseUrl.replace(/\/+$/, '')}/chat/completions`;

    const upstreamRes = await fetch(url, {
      method: 'POST',
      headers: this.upstreamHeaders(upstream.apiKey),
      body: JSON.stringify(payload),
    });

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
