"use strict";
/**
 * @deprecated Replaced by RuntimeManager (runtime-manager.ts).
 * Kept for reference only -- not imported by main.ts.
 *
 * The old PythonSidecar ran `nls.engine.serve_local` for local
 * inference with a locally-loaded model.  The new RuntimeManager
 * runs the full `server.main:app` in remote mode instead.
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
exports.PythonSidecar = void 0;
const child_process_1 = require("child_process");
const electron_1 = require("electron");
const path = __importStar(require("path"));
const http = __importStar(require("http"));
// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------
const DEFAULT_PORT = 9222;
const DEFAULT_MODEL = 'unsloth/Meta-Llama-3.1-8B-Instruct';
const HEALTH_CHECK_INTERVAL = 5_000; // 5 seconds
const STARTUP_TIMEOUT = 120_000; // 2 minutes (model loading can be slow)
// ---------------------------------------------------------------------------
// Python Sidecar
// ---------------------------------------------------------------------------
class PythonSidecar {
    process = null;
    port = DEFAULT_PORT;
    model = null;
    healthTimer = null;
    _running = false;
    /**
     * Start the Python sidecar process.
     */
    async start(model) {
        if (this.process) {
            return; // Already running
        }
        this.model = model || DEFAULT_MODEL;
        this.port = DEFAULT_PORT;
        // Determine the NLS project root (two levels up from dist-electron/)
        const nlsRoot = path.resolve(__dirname, '..', '..');
        // Spawn the Python process
        this.process = (0, child_process_1.spawn)('python', [
            '-m',
            'nls.engine.serve_local',
            '--port',
            String(this.port),
            '--model',
            this.model,
        ], {
            cwd: nlsRoot,
            stdio: ['pipe', 'pipe', 'pipe'],
            env: {
                ...process.env,
                PYTHONUNBUFFERED: '1',
            },
        });
        // Forward stdout/stderr to renderer as log events
        this.process.stdout?.on('data', (data) => {
            const message = data.toString();
            this.broadcastLog('stdout', message);
        });
        this.process.stderr?.on('data', (data) => {
            const message = data.toString();
            this.broadcastLog('stderr', message);
        });
        this.process.on('exit', (code) => {
            this._running = false;
            this.process = null;
            this.stopHealthCheck();
            this.broadcastStatus();
            this.broadcastLog('system', `Python sidecar exited with code ${code}`);
        });
        this.process.on('error', (err) => {
            this._running = false;
            this.broadcastLog('system', `Sidecar error: ${err.message}`);
        });
        // Wait for the sidecar to become healthy
        await this.waitForHealth();
        this._running = true;
        this.startHealthCheck();
        this.broadcastStatus();
    }
    /**
     * Stop the Python sidecar process.
     */
    async stop() {
        this.stopHealthCheck();
        if (this.process) {
            this.process.kill('SIGTERM');
            // Give it 5 seconds to shut down gracefully
            await new Promise((resolve) => {
                const timeout = setTimeout(() => {
                    if (this.process) {
                        this.process.kill('SIGKILL');
                    }
                    resolve();
                }, 5_000);
                this.process?.on('exit', () => {
                    clearTimeout(timeout);
                    resolve();
                });
            });
            this.process = null;
        }
        this._running = false;
        this.broadcastStatus();
    }
    /**
     * Get current sidecar status.
     */
    getStatus() {
        return {
            running: this._running,
            port: this.port,
            pid: this.process?.pid ?? null,
            model: this.model,
        };
    }
    // ─── Health checking ────────────────────────────────────────────────
    async waitForHealth() {
        const start = Date.now();
        while (Date.now() - start < STARTUP_TIMEOUT) {
            if (await this.checkHealth()) {
                return;
            }
            await this.sleep(2_000);
        }
        throw new Error(`Python sidecar failed to start within ${STARTUP_TIMEOUT / 1000}s`);
    }
    checkHealth() {
        return new Promise((resolve) => {
            const req = http.get(`http://localhost:${this.port}/health`, { timeout: 3_000 }, (res) => {
                resolve(res.statusCode === 200);
            });
            req.on('error', () => resolve(false));
            req.on('timeout', () => {
                req.destroy();
                resolve(false);
            });
        });
    }
    startHealthCheck() {
        this.healthTimer = setInterval(async () => {
            const healthy = await this.checkHealth();
            if (!healthy && this._running) {
                this._running = false;
                this.broadcastStatus();
                this.broadcastLog('system', 'Python sidecar health check failed');
            }
        }, HEALTH_CHECK_INTERVAL);
    }
    stopHealthCheck() {
        if (this.healthTimer) {
            clearInterval(this.healthTimer);
            this.healthTimer = null;
        }
    }
    // ─── Helpers ────────────────────────────────────────────────────────
    broadcastLog(level, message) {
        for (const win of electron_1.BrowserWindow.getAllWindows()) {
            win.webContents.send('sidecar:log', { level, message });
        }
    }
    broadcastStatus() {
        for (const win of electron_1.BrowserWindow.getAllWindows()) {
            win.webContents.send('sidecar:status-changed', this.getStatus());
        }
    }
    sleep(ms) {
        return new Promise((resolve) => setTimeout(resolve, ms));
    }
}
exports.PythonSidecar = PythonSidecar;
//# sourceMappingURL=python-sidecar.js.map