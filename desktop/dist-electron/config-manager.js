"use strict";
/**
 * NLS Desktop App -- Configuration Manager
 *
 * Persists user configuration (GX10 URLs, secrets, model name, etc.)
 * in the Electron userData directory.  The renderer reads/writes config
 * via IPC, and the RuntimeManager reads it to set environment variables
 * for the Python sidecar process.
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
// ---------------------------------------------------------------------------
// Defaults
// ---------------------------------------------------------------------------
const DEFAULT_CONFIG = {
    vllmUrl: 'http://brain.babo.agency:8000',
    gpuWorkerUrl: 'http://brain.babo.agency:8443',
    gpuWorkerSecret: 'nls-gpu-worker-2026',
    nestjsUrl: 'https://api.babo.agency',
    hfModel: '/root/.cache/huggingface/hub/qwen35-nls-512e-fp8',
    runtimePort: 9222,
    setupComplete: false,
    gx10Host: 'brain.babo.agency',
};
// ---------------------------------------------------------------------------
// ConfigManager
// ---------------------------------------------------------------------------
class ConfigManager {
    config;
    configPath;
    constructor() {
        this.configPath = path.join(electron_1.app.getPath('userData'), 'nls-config.json');
        this.config = { ...DEFAULT_CONFIG };
        this.load();
    }
    get() {
        return { ...this.config };
    }
    set(partial) {
        this.config = { ...this.config, ...partial };
        // Auto-derive gx10Host from vllmUrl when it changes
        if (partial.vllmUrl) {
            try {
                const url = new URL(partial.vllmUrl);
                this.config.gx10Host = url.hostname;
            }
            catch {
                // Keep existing gx10Host
            }
        }
        this.save();
        return { ...this.config };
    }
    reset() {
        this.config = { ...DEFAULT_CONFIG };
        this.save();
        return { ...this.config };
    }
    isSetupComplete() {
        return this.config.setupComplete;
    }
    /**
     * Build environment variables for the Python agent runtime process.
     */
    getRuntimeEnv() {
        const dataDir = electron_1.app.isPackaged
            ? path.join(electron_1.app.getPath('userData'), 'data')
            : path.join(path.resolve(__dirname, '..', '..'), 'data');
        return {
            NLS_VLLM_BASE_URL: this.config.vllmUrl,
            NLS_HF_MODEL: this.config.hfModel,
            NLS_TRAINING_MODE: 'remote',
            NLS_GPU_WORKER_URL: this.config.gpuWorkerUrl,
            NLS_GPU_WORKER_SECRET: this.config.gpuWorkerSecret,
            NLS_N_TRAIN_WORKERS: '0',
            NLS_SLEEP_ENABLED: 'true',
            NLS_EDUCATION_ENABLED: 'false',
            NLS_MOE_ENABLED: 'true',
            NLS_MOE_V2: 'true',
            NLS_MOE_EXPERTS_DIR: path.join(dataDir, 'moe_experts'),
            NLS_DEFAULT_GENESIS: 'moe-v1',
            NLS_SHARED_SECRET: '',
            GX10_SHARED_SECRET: 'nls-dev-secret',
            NLS_HOST: '127.0.0.1',
            NLS_PORT: String(this.config.runtimePort),
            NLS_DATA_DIR: dataDir,
            NESTJS_URL: this.config.nestjsUrl,
            NLS_BROWSER_CDP_URL: `http://127.0.0.1:9245`,
            PYTHONUNBUFFERED: '1',
        };
    }
    // ─── Persistence ──────────────────────────────────────────────
    load() {
        try {
            if (fs.existsSync(this.configPath)) {
                const data = JSON.parse(fs.readFileSync(this.configPath, 'utf-8'));
                this.config = { ...DEFAULT_CONFIG, ...data };
            }
        }
        catch {
            this.config = { ...DEFAULT_CONFIG };
        }
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
            // Silently fail -- config will use defaults next launch
        }
    }
}
exports.ConfigManager = ConfigManager;
//# sourceMappingURL=config-manager.js.map