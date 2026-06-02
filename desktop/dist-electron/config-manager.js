"use strict";
/**
 * NLS Desktop App -- Configuration Manager
 *
 * Persists user configuration (inference URL, model, NestJS backend)
 * in the Electron userData directory.
 */
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || function (mod) {
    if (mod && mod.__esModule) return mod;
    var result = {};
    if (mod != null) for (var k in mod) if (k !== "default" && Object.prototype.hasOwnProperty.call(mod, k)) __createBinding(result, mod, k);
    __setModuleDefault(result, mod);
    return result;
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.ConfigManager = void 0;
const electron_1 = require("electron");
const fs = __importStar(require("fs"));
const path = __importStar(require("path"));
const capability_types_1 = require("./capability-types");
const DEFAULT_CONFIG = {
    inferenceUrl: 'https://openrouter.ai/api/v1',
    inferenceModel: 'openai/gpt-4o-mini',
    inferenceApiKey: '',
    nestjsUrl: 'https://api.babo.agency',
    runtimePort: 9222,
    runtimeSharedSecret: 'nls-dev-secret',
    setupComplete: false,
    capabilityProfile: { ...capability_types_1.DEFAULT_CAPABILITY_PROFILE },
};
class ConfigManager {
    config;
    configPath;
    constructor() {
        this.configPath = path.join(electron_1.app.getPath('userData'), 'nls-config.json');
        this.config = { ...DEFAULT_CONFIG };
        this.load();
    }
    get() {
        return {
            ...this.config,
            capabilityProfile: this.config.capabilityProfile
                ? JSON.parse(JSON.stringify(this.config.capabilityProfile))
                : undefined,
        };
    }
    set(partial) {
        const next = { ...partial };
        if (typeof next.inferenceUrl === 'string') {
            next.inferenceUrl = ConfigManager.normalizeInferenceUrl(next.inferenceUrl);
        }
        if (next.capabilityProfile) {
            next.capabilityProfile = (0, capability_types_1.sanitizeCapabilityProfile)(next.capabilityProfile);
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
    static nestjsApiBase(nestjsUrl) {
        const base = nestjsUrl.trim().replace(/\/+$/, '');
        return base.endsWith('/api') ? base : `${base}/api`;
    }
    /** Local vLLM/Ollama: host root only. Cloud providers keep a `/v1` suffix in config. */
    static normalizeInferenceUrl(url) {
        const trimmed = url.trim().replace(/\/+$/, '');
        if (!trimmed.endsWith('/v1')) {
            return trimmed;
        }
        try {
            const host = new URL(trimmed).hostname.toLowerCase();
            const cloud = host.includes('openrouter.ai') ||
                host.endsWith('.openai.com') ||
                host.includes('openai.azure.com') ||
                host.includes('api.babo.agency');
            if (cloud) {
                return trimmed;
            }
        }
        catch {
            return trimmed;
        }
        return trimmed.slice(0, -3);
    }
    reset() {
        this.config = { ...DEFAULT_CONFIG };
        this.save();
        return { ...this.config };
    }
    isSetupComplete() {
        return this.config.setupComplete;
    }
    shouldPrefetchVision() {
        const p = this.config.capabilityProfile;
        if (!p)
            return false;
        const strat = p.visualCortex.strategy ?? 'off';
        return (strat === 'dedicated_vlm_local' &&
            p.visualCortex.tier !== 'off');
    }
    getRuntimeEnv() {
        const dataDir = electron_1.app.isPackaged
            ? path.join(electron_1.app.getPath('userData'), 'data')
            : path.join(path.resolve(__dirname, '..', '..'), 'data');
        const profile = this.config.capabilityProfile ?? capability_types_1.DEFAULT_CAPABILITY_PROFILE;
        const env = {
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
            ...(0, capability_types_1.capabilityProfileToRuntimeEnv)(profile, {
                inferenceApiKey: this.config.inferenceApiKey,
                runtimePort: this.config.runtimePort,
                nestjsApiBase: ConfigManager.nestjsApiBase(this.config.nestjsUrl),
            }),
        };
        const sharedSecret = this.config.runtimeSharedSecret?.trim() ||
            DEFAULT_CONFIG.runtimeSharedSecret ||
            '';
        if (sharedSecret) {
            env.NLS_SHARED_SECRET = sharedSecret;
            env.RUNTIME_SHARED_SECRET = sharedSecret;
        }
        const p = profile;
        const needsLegacyGpuUrl = p.visualCortex.tier === 'hosted_babo' ||
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
    reconcileGpuWorkerFields() {
        const p = this.config.capabilityProfile;
        if (!p)
            return;
        const allLocal = p.visualCortex.tier === 'self_local' &&
            p.transcribe.tier === 'self_local' &&
            p.embeddings.tier === 'self_local';
        if (allLocal) {
            delete this.config.gpuWorkerUrl;
            delete this.config.gpuWorkerSecret;
        }
    }
    load() {
        try {
            if (fs.existsSync(this.configPath)) {
                const data = JSON.parse(fs.readFileSync(this.configPath, 'utf-8'));
                const merged = { ...DEFAULT_CONFIG, ...this.migrateLegacy(data) };
                if (merged.inferenceUrl) {
                    merged.inferenceUrl = ConfigManager.normalizeInferenceUrl(merged.inferenceUrl);
                }
                if (!merged.capabilityProfile) {
                    merged.capabilityProfile = { ...capability_types_1.DEFAULT_CAPABILITY_PROFILE };
                }
                else {
                    merged.capabilityProfile = (0, capability_types_1.sanitizeCapabilityProfile)(merged.capabilityProfile);
                    if (merged.capabilityProfile.inference.model) {
                        merged.inferenceModel = merged.capabilityProfile.inference.model;
                    }
                }
                if (!merged.runtimeSharedSecret?.trim()) {
                    merged.runtimeSharedSecret = DEFAULT_CONFIG.runtimeSharedSecret;
                }
                this.config = merged;
                this.reconcileGpuWorkerFields();
                const prevModel = data
                    .capabilityProfile?.inference?.model;
                if (merged.capabilityProfile.inference.model !== prevModel) {
                    this.save();
                }
            }
        }
        catch {
            this.config = { ...DEFAULT_CONFIG };
        }
    }
    migrateLegacy(data) {
        const out = { ...data };
        if (!out.inferenceUrl && typeof data['vllmUrl'] === 'string') {
            out.inferenceUrl = data['vllmUrl'];
        }
        if (!out.inferenceModel && typeof data['hfModel'] === 'string') {
            const legacy = data['hfModel'];
            if (!legacy.startsWith('/')) {
                out.inferenceModel = legacy;
            }
        }
        return out;
    }
    save() {
        try {
            const dir = path.dirname(this.configPath);
            if (!fs.existsSync(dir)) {
                fs.mkdirSync(dir, { recursive: true });
            }
            fs.writeFileSync(this.configPath, JSON.stringify(this.config, null, 2), 'utf-8');
        }
        catch {
            // use defaults next launch
        }
    }
}
exports.ConfigManager = ConfigManager;
//# sourceMappingURL=config-manager.js.map