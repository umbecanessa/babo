import {
  HttpException,
  HttpStatus,
  Injectable,
  Logger,
} from '@nestjs/common';
import { randomUUID } from 'crypto';
import { CloudAuthContext } from './cloud-auth.types';
import { CloudUpstreamService } from './cloud-upstream.service';
import { CloudUsageService } from './cloud-usage.service';
import { ApiKeysService } from '../api-keys/api-keys.service';
import { CloudRateLimiterService } from './cloud-rate-limiter.service';
import { ConfigService } from '@nestjs/config';

@Injectable()
export class GpuService {
  private readonly logger = new Logger(GpuService.name);
  private readonly defaultRpm: number;

  constructor(
    private upstream: CloudUpstreamService,
    private usage: CloudUsageService,
    private apiKeys: ApiKeysService,
    private rateLimiter: CloudRateLimiterService,
    config: ConfigService,
  ) {
    this.defaultRpm = Number(config.get('INFERENCE_DEFAULT_RPM') || 120);
  }

  private async assertRateLimit(auth: CloudAuthContext): Promise<void> {
    let rpm = this.defaultRpm;
    if (auth.apiKeyId) {
      const keyRpm = await this.apiKeys.getRateLimitRpm(auth.apiKeyId);
      if (keyRpm != null) rpm = keyRpm;
    }
    this.rateLimiter.assertWithinLimit(auth, rpm);
  }

  async proxyTranscribe(
    auth: CloudAuthContext,
    file: Express.Multer.File,
  ): Promise<unknown> {
    await this.assertRateLimit(auth);
    if (!this.upstream.isGpuConfigured()) {
      throw new HttpException(
        'GPU upstream not configured',
        HttpStatus.SERVICE_UNAVAILABLE,
      );
    }

    const url = `${this.upstream.transcribeBase}/transcribe`;
    const form = new FormData();
    form.append(
      'audio',
      new Blob([new Uint8Array(file.buffer)], { type: file.mimetype }),
      file.originalname,
    );

    const res = await fetch(url, {
      method: 'POST',
      headers: this.upstream.gpuHeaders(),
      body: form,
    });
    const text = await res.text();
    if (!res.ok) {
      throw new HttpException(text || res.statusText, res.status);
    }
    const data = JSON.parse(text);
    await this.usage.record({
      auth,
      requestId: randomUUID(),
      workload: 'transcribe',
      placement: 'hosted_babo',
      model: 'whisper',
      route: 'transcribe',
      provider: 'babo',
      usage: {
        promptTokens: 0,
        completionTokens: 0,
        totalTokens: Math.ceil(Number(data.duration || 1) * 10),
      },
    });
    return data;
  }

  async proxyVisionDescribe(
    auth: CloudAuthContext,
    body: Record<string, unknown>,
  ): Promise<unknown> {
    await this.assertRateLimit(auth);
    if (!this.upstream.isGpuConfigured()) {
      throw new HttpException(
        'GPU upstream not configured',
        HttpStatus.SERVICE_UNAVAILABLE,
      );
    }

    const url = `${this.upstream.visionBase}/vision/describe`;
    const res = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...this.upstream.gpuHeaders(),
      },
      body: JSON.stringify(body),
    });
    const text = await res.text();
    if (!res.ok) {
      throw new HttpException(text || res.statusText, res.status);
    }
    const data = JSON.parse(text);
    await this.usage.record({
      auth,
      requestId: randomUUID(),
      workload: 'vision',
      placement: 'hosted_babo',
      model: 'moondream',
      route: 'vision/describe',
      provider: 'babo',
      usage: { promptTokens: 0, completionTokens: 0, totalTokens: 1 },
    });
    return data;
  }

  async proxyEmbed(
    auth: CloudAuthContext,
    body: Record<string, unknown>,
  ): Promise<unknown> {
    await this.assertRateLimit(auth);
    if (!this.upstream.isGpuConfigured()) {
      throw new HttpException(
        'GPU upstream not configured',
        HttpStatus.SERVICE_UNAVAILABLE,
      );
    }

    const url = `${this.upstream.embedBase}/embed`;
    const res = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...this.upstream.gpuHeaders(),
      },
      body: JSON.stringify(body),
    });
    const text = await res.text();
    if (!res.ok) {
      throw new HttpException(text || res.statusText, res.status);
    }
    const data = JSON.parse(text);
    await this.usage.record({
      auth,
      requestId: randomUUID(),
      workload: 'embed',
      placement: 'hosted_babo',
      model: 'embed',
      route: 'embed',
      provider: 'babo',
      usage: { promptTokens: 0, completionTokens: 0, totalTokens: 1 },
    });
    return data;
  }

  async health(): Promise<{ ok: boolean; inference: boolean; gpu: boolean }> {
    return {
      ok: this.upstream.isInferenceConfigured(),
      inference: this.upstream.isInferenceConfigured(),
      gpu: this.upstream.isGpuConfigured(),
    };
  }
}
