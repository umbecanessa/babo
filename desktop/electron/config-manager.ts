/**
 * NLS Desktop App -- Configuration Manager
 *
 * Persists user configuration (inference URL, model, NestJS backend)
 * in the Electron userData directory.
 */

import { app } from 'electron';
import * as fs from 'fs';
import * as path from 'path';

import type { CapabilityProfile } from './capability-types';
import {
  DEFAULT_CAPABILITY_PROFILE,
  capabilityProfileToRuntimeEnv,
  sanitizeCapabilityProfile,
} from './capability-types';

export interface NlsConfig {
  /** OpenAI-compatible inference API base URL */
  inferenceUrl: string;

  /** Model id sent to the inference API */
  inferenceModel: string;

  /** Optional API key for the inference provider */
  inferenceApiKey: string;

  /** NestJS backend URL for auth/admin */
  nestjsUrl: string;

  /** Local agent runtime port */
  runtimePort: number;

  /** Whether first-run setup has been completed */
  setupComplete: boolean;

  /** Composable capability placements (onboarding wizard) */
  capabilityProfile?: CapabilityProfile;

  /** @deprecated legacy field — migrated on load */
  vllmUrl?: string;
  hfModel?: string;
  gpuWorkerUrl?: string;
  gpuWorkerSecret?: string;
  /** Must match NestJS `RUNTIME_SHARED_SECRET` for channel relay WS auth */
  runtimeSharedSecret?: string;
  runtimeHost?: string;
}

const DEFAULT_CONFIG: NlsConfig = {
  inferenceUrl: 'https://openrouter.ai/api/v1',
  inferenceModel: 'openai/gpt-4o-mini',
  inferenceApiKey: '',
  nestjsUrl: 'https://api.babo.agency',
  runtimePort: 9222,
  runtimeSharedSecret: 'nls-dev-secret',
  setupComplete: false,
  capabilityProfile: { ...DEFAULT_CAPABILITY_PROFILE },
};

export class ConfigManager {
  private config: NlsConfig;
  private configPath: string;

  constructor() {
    this.configPath = path.join(app.getPath('userData'), 'nls-config.json');
    this.config = { ...DEFAULT_CONFIG };
    this.load();
  }

  get(): NlsConfig {
    return {
      ...this.config,
      capabilityProfile: this.config.capabilityProfile
        ? JSON.parse(JSON.stringify(this.config.capabilityProfile))
        : undefined,
    };
  }

  set(partial: Partial<NlsConfig>): NlsConfig {
    const next = { ...partial };
    if (typeof next.inferenceUrl === 'string') {
      next.inferenceUrl = ConfigManager.normalizeInferenceUrl(next.inferenceUrl);
    }
    if (next.capabilityProfile) {
      next.capabilityProfile = sanitizeCapabilityProfile(next.capabilityProfile);
      const p = next.capabilityProfile;
      if (p.inference?.url) {
        next.inferenceUrl = ConfigManager.normalizeInferenceUrl(p.inference.url);
      }
      if (p.inference?.model) {
        next.inferenceModel = p.inference.model;
      }
    }
    this.config = { ...this.config, ...next };
    this.reconcileGpuWorkerFields();
    this.save();
    return { ...this.config };
  }

  /** NestJS global prefix (`backend/src/main.ts` → `setGlobalPrefix('api')`). */
  static nestjsApiBase(nestjsUrl: string): string {
    const base = nestjsUrl.trim().replace(/\/+$/, '');
    return base.endsWith('/api') ? base : `${base}/api`;
  }

  /** Local vLLM/Ollama: host root only. Cloud providers keep a `/v1` suffix in config. */
  static normalizeInferenceUrl(url: string): string {
    const trimmed = url.trim().replace(/\/+$/, '');
    if (!trimmed.endsWith('/v1')) {
      return trimmed;
    }
    try {
      const host = new URL(trimmed).hostname.toLowerCase();
      const cloud =
        host.includes('openrouter.ai') ||
        host.endsWith('.openai.com') ||
        host.includes('openai.azure.com') ||
        host.includes('api.babo.agency');
      if (cloud) {
        return trimmed;
      }
    } catch {
      return trimmed;
    }
    return trimmed.slice(0, -3);
  }

  reset(): NlsConfig {
    this.config = { ...DEFAULT_CONFIG };
    this.save();
    return { ...this.config };
  }

  isSetupComplete(): boolean {
    return this.config.setupComplete;
  }

  /** Agent runtime data root (`NLS_DATA_DIR` when the runtime is spawned). */
  getDataDir(): string {
    const fromEnv = process.env['NLS_DATA_DIR']?.trim();
    if (fromEnv) return fromEnv;
    if (app.isPackaged) {
      return path.join(app.getPath('userData'), 'data');
    }
    return path.join(path.resolve(__dirname, '..', '..'), 'data');
  }

  shouldPrefetchVision(): boolean {
    const p = this.config.capabilityProfile;
    if (!p) return false;
    const strat = p.visualCortex.strategy ?? 'off';
    return (
      strat === 'dedicated_vlm_local' &&
      p.visualCortex.tier !== 'off'
    );
  }

  getRuntimeEnv(): Record<string, string> {
    const dataDir = this.getDataDir();

    const profile =
      this.config.capabilityProfile ?? DEFAULT_CAPABILITY_PROFILE;

    const env: Record<string, string> = {
      NLS_PRODUCT_MODE: '1',
      NLS_VLLM_BASE_URL: this.config.inferenceUrl,
      NLS_HF_MODEL: this.config.inferenceModel,
      NLS_INFERENCE_API_KEY: this.config.inferenceApiKey || '',
      NLS_TRAINING_MODE: 'none',
      NLS_N_TRAIN_WORKERS: '0',
      NLS_SLEEP_ENABLED: 'true',
      NLS_EDUCATION_ENABLED: 'false',
      NLS_DEFAULT_GENESIS: 'standard-v1',
      NLS_HOST: '127.0.0.1',
      NLS_PORT: String(this.config.runtimePort),
      NLS_DATA_DIR: dataDir,
      NESTJS_URL: this.config.nestjsUrl,
      NLS_BROWSER_CDP_URL: 'http://127.0.0.1:9245',
      PYTHONUNBUFFERED: '1',
      ...capabilityProfileToRuntimeEnv(profile, {
        inferenceApiKey: this.config.inferenceApiKey,
        runtimePort: this.config.runtimePort,
        nestjsApiBase: ConfigManager.nestjsApiBase(this.config.nestjsUrl),
      }),
    };

    const sharedSecret =
      this.config.runtimeSharedSecret?.trim() ||
      DEFAULT_CONFIG.runtimeSharedSecret ||
      '';
    if (sharedSecret) {
      env.NLS_SHARED_SECRET = sharedSecret;
      env.RUNTIME_SHARED_SECRET = sharedSecret;
    }

    const p = profile;
    const needsLegacyGpuUrl =
      p.visualCortex.tier === 'hosted_babo' ||
      p.visualCortex.tier === 'self_lan' ||
      p.transcribe.tier === 'hosted_babo' ||
      p.transcribe.tier === 'self_lan' ||
      p.embeddings.tier === 'hosted_babo' ||
      p.embeddings.tier === 'self_lan';

    if (needsLegacyGpuUrl && this.config.gpuWorkerUrl) {
      env.NLS_GPU_WORKER_URL = env.NLS_GPU_WORKER_URL || this.config.gpuWorkerUrl;
    }
    if (needsLegacyGpuUrl && this.config.gpuWorkerSecret) {
      env.NLS_GPU_WORKER_SECRET =
        env.NLS_GPU_WORKER_SECRET || this.config.gpuWorkerSecret;
    }

    return env;
  }

  /** Drop stale cloud GPU URL when every workload is local (common after profile edits). */
  private reconcileGpuWorkerFields(): void {
    const p = this.config.capabilityProfile;
    if (!p) return;
    const allLocal =
      p.visualCortex.tier === 'self_local' &&
      p.transcribe.tier === 'self_local' &&
      p.embeddings.tier === 'self_local';
    if (allLocal) {
      delete this.config.gpuWorkerUrl;
      delete this.config.gpuWorkerSecret;
    }
  }

  private load(): void {
    try {
      if (fs.existsSync(this.configPath)) {
        const data = JSON.parse(fs.readFileSync(this.configPath, 'utf-8'));
        const merged = { ...DEFAULT_CONFIG, ...this.migrateLegacy(data) };
        if (merged.inferenceUrl) {
          merged.inferenceUrl = ConfigManager.normalizeInferenceUrl(merged.inferenceUrl);
        }
        if (!merged.capabilityProfile) {
          merged.capabilityProfile = { ...DEFAULT_CAPABILITY_PROFILE };
        } else {
          merged.capabilityProfile = sanitizeCapabilityProfile(
            merged.capabilityProfile,
          );
          const inf = merged.capabilityProfile.inference;
          if (inf.url && inf.tier !== 'hosted_babo' && inf.tier !== 'byok_cloud') {
            merged.inferenceUrl = ConfigManager.normalizeInferenceUrl(inf.url);
          }
          if (inf.model) {
            merged.inferenceModel = inf.model;
          }
        }
        if (!merged.runtimeSharedSecret?.trim()) {
          merged.runtimeSharedSecret = DEFAULT_CONFIG.runtimeSharedSecret;
        }
        this.config = merged;
        this.reconcileGpuWorkerFields();
        const prevProfile = (data as { capabilityProfile?: CapabilityProfile })
          .capabilityProfile;
        const prevTier = prevProfile?.inference?.tier;
        const prevModel = prevProfile?.inference?.model;
        const prevUrl = (data as { inferenceUrl?: string }).inferenceUrl;
        if (
          merged.capabilityProfile.inference.tier !== prevTier ||
          merged.capabilityProfile.inference.model !== prevModel ||
          merged.inferenceUrl !== prevUrl
        ) {
          this.save();
        }
      }
    } catch {
      this.config = { ...DEFAULT_CONFIG };
    }
  }

  private migrateLegacy(data: Record<string, unknown>): Partial<NlsConfig> {
    const out: Partial<NlsConfig> = { ...data } as Partial<NlsConfig>;
    if (!out.inferenceUrl && typeof data['vllmUrl'] === 'string') {
      out.inferenceUrl = data['vllmUrl'] as string;
    }
    if (!out.inferenceModel && typeof data['hfModel'] === 'string') {
      const legacy = data['hfModel'] as string;
      if (!legacy.startsWith('/')) {
        out.inferenceModel = legacy;
      }
    }
    return out;
  }

  private save(): void {
    try {
      const dir = path.dirname(this.configPath);
      if (!fs.existsSync(dir)) {
        fs.mkdirSync(dir, { recursive: true });
      }
      fs.writeFileSync(
        this.configPath,
        JSON.stringify(this.config, null, 2),
        'utf-8',
      );
    } catch {
      // use defaults next launch
    }
  }
}
