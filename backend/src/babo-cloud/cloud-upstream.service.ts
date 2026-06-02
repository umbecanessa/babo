import { Injectable } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';

export type InferenceProviderId =
  | 'hosted'
  | 'openai'
  | 'anthropic'
  | 'openrouter';

export const PROVIDER_OPENAI_BASE_URLS: Record<string, string> = {
  openai: 'https://api.openai.com/v1',
  openrouter: 'https://openrouter.ai/api/v1',
};

@Injectable()
export class CloudUpstreamService {
  readonly inferenceUpstream: string;
  readonly inferenceUpstreamKey: string;
  readonly gpuBase: string;
  readonly gpuSecret: string;
  readonly transcribeBase: string;
  readonly visionBase: string;
  readonly embedBase: string;
  readonly cloudMode: boolean;
  /** Babo-operated OpenRouter key for trial / resold inference (Railway secret). */
  readonly platformOpenRouterKey: string;
  /** vLLM model id on GX10 when desktop sends ``babo-hosted``. */
  readonly inferenceUpstreamModel: string;

  constructor(private config: ConfigService) {
    const inf =
      config.get<string>('INFERENCE_UPSTREAM_URL') ||
      config.get<string>('BABO_INFERENCE_UPSTREAM_URL') ||
      '';
    this.inferenceUpstream = inf.replace(/\/+$/, '');
    this.inferenceUpstreamKey =
      config.get<string>('INFERENCE_UPSTREAM_API_KEY') ||
      config.get<string>('BABO_INFERENCE_UPSTREAM_API_KEY') ||
      '';

    const gpu =
      config.get<string>('GPU_UPSTREAM_URL') ||
      config.get<string>('BABO_GPU_UPSTREAM_URL') ||
      this.inferenceUpstream.replace(/\/v1$/i, '');
    this.gpuBase = gpu.replace(/\/+$/, '');
    this.gpuSecret =
      config.get<string>('GPU_UPSTREAM_SECRET') ||
      config.get<string>('BABO_GPU_UPSTREAM_SECRET') ||
      '';

    this.transcribeBase = (
      config.get<string>('GPU_TRANSCRIBE_UPSTREAM_URL') ||
      this.gpuBase
    ).replace(/\/+$/, '');
    this.visionBase = (
      config.get<string>('GPU_VISION_UPSTREAM_URL') || this.gpuBase
    ).replace(/\/+$/, '');
    this.embedBase = (
      config.get<string>('GPU_EMBED_UPSTREAM_URL') || this.gpuBase
    ).replace(/\/+$/, '');

    this.cloudMode = config.get<string>('BABO_CLOUD_MODE') !== 'false';
    this.platformOpenRouterKey =
      config.get<string>('PLATFORM_OPENROUTER_API_KEY') ||
      config.get<string>('BABO_OPENROUTER_API_KEY') ||
      '';
    this.inferenceUpstreamModel = (
      config.get<string>('INFERENCE_UPSTREAM_MODEL') ||
      config.get<string>('BABO_INFERENCE_UPSTREAM_MODEL') ||
      ''
    ).trim();
  }

  isResoldInferenceConfigured(): boolean {
    return this.platformOpenRouterKey.length > 0;
  }

  inferenceApiBase(): string {
    const base = this.inferenceUpstream.endsWith('/v1')
      ? this.inferenceUpstream
      : `${this.inferenceUpstream}/v1`;
    return base;
  }

  isInferenceConfigured(): boolean {
    return this.inferenceUpstream.length > 0;
  }

  isGpuConfigured(): boolean {
    return this.gpuBase.length > 0;
  }

  gpuHeaders(): Record<string, string> {
    const h: Record<string, string> = {};
    if (this.gpuSecret) {
      h['X-GPU-Worker-Secret'] = this.gpuSecret;
    }
    return h;
  }
}
