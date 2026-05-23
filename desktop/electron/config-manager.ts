/**
 * NLS Desktop App -- Configuration Manager
 *
 * Persists user configuration (inference URL, model, NestJS backend)
 * in the Electron userData directory.
 */

import { app } from 'electron';
import * as fs from 'fs';
import * as path from 'path';

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

  /** @deprecated legacy field — migrated on load */
  vllmUrl?: string;
  hfModel?: string;
  gpuWorkerUrl?: string;
  gpuWorkerSecret?: string;
  runtimeHost?: string;
}

const DEFAULT_CONFIG: NlsConfig = {
  inferenceUrl: 'https://openrouter.ai/api/v1',
  inferenceModel: 'openai/gpt-4o-mini',
  inferenceApiKey: '',
  nestjsUrl: 'http://localhost:3000',
  runtimePort: 9222,
  setupComplete: false,
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
    return { ...this.config };
  }

  set(partial: Partial<NlsConfig>): NlsConfig {
    this.config = { ...this.config, ...partial };
    this.save();
    return { ...this.config };
  }

  reset(): NlsConfig {
    this.config = { ...DEFAULT_CONFIG };
    this.save();
    return { ...this.config };
  }

  isSetupComplete(): boolean {
    return this.config.setupComplete;
  }

  getRuntimeEnv(): Record<string, string> {
    const dataDir = app.isPackaged
      ? path.join(app.getPath('userData'), 'data')
      : path.join(path.resolve(__dirname, '..', '..'), 'data');

    return {
      NLS_PRODUCT_MODE: '1',
      NLS_VLLM_BASE_URL: this.config.inferenceUrl,
      NLS_HF_MODEL: this.config.inferenceModel,
      NLS_INFERENCE_API_KEY: this.config.inferenceApiKey || '',
      NLS_TRAINING_MODE: 'none',
      NLS_N_TRAIN_WORKERS: '0',
      NLS_SLEEP_ENABLED: 'true',
      NLS_EDUCATION_ENABLED: 'false',
      NLS_DEFAULT_GENESIS: 'standard-v1',
      NLS_SHARED_SECRET: '',
      NLS_HOST: '127.0.0.1',
      NLS_PORT: String(this.config.runtimePort),
      NLS_DATA_DIR: dataDir,
      NESTJS_URL: this.config.nestjsUrl,
      NLS_BROWSER_CDP_URL: 'http://127.0.0.1:9245',
      PYTHONUNBUFFERED: '1',
    };
  }

  private load(): void {
    try {
      if (fs.existsSync(this.configPath)) {
        const data = JSON.parse(fs.readFileSync(this.configPath, 'utf-8'));
        this.config = { ...DEFAULT_CONFIG, ...this.migrateLegacy(data) };
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

