"use strict";
/**
 * NLS Desktop App -- Agent Runtime Manager
 *
 * Spawns and manages the local Python agent runtime process.
 * The runtime is the full NLS FastAPI server (server.main:app)
 * configured in remote mode -- no local GPU required.
 *
 * Replaces the old PythonSidecar which ran a lightweight
 * serve_local module.  The full server provides all endpoints
 * (agents, chat, tools, brain, etc.) that the Angular frontend
 * connects to directly.
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
exports.RuntimeManager = void 0;
const child_process_1 = require("child_process");
const electron_1 = require("electron");
const fs = __importStar(require("fs"));
const http = __importStar(require("http"));
const path = __importStar(require("path"));
const config_manager_1 = require("./config-manager");
// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------
const HEALTH_CHECK_INTERVAL = 5_000;
const STARTUP_TIMEOUT = 180_000;
const LEASE_HEARTBEAT_INTERVAL = 60_000;
// ---------------------------------------------------------------------------
// RuntimeManager
// ---------------------------------------------------------------------------
class RuntimeManager {
    config;
    venv;
    process = null;
    healthTimer = null;
    leaseTimer = null;
    _running = false;
    _startedAt = 0;
    _agentCount = 0;
    _agentEnergy = {};
    _lastError = null;
    logBuffer = [];
    maxLogLines = 500;
    logStream = null;
    activeLeaseAgents = [];
    constructor(config, venv) {
        this.config = config;
        this.venv = venv;
    }
    /**
     * Start the Python agent runtime.
     */
    async start() {
        if (this.process) {
            return; // Already running
        }
        if (!this.venv.isReady()) {
            throw new Error('Python environment not set up. Run setup first.');
        }
        const pythonBin = this.venv.getVenvPython();
        const nlsRoot = this.venv.getNlsRoot();
        const cfg = this.config.get();
        const env = this.config.getRuntimeEnv();
        this._lastError = null;
        // Open persistent log file
        const logPath = path.join(electron_1.app.getPath('userData'), 'runtime.log');
        try {
            this.logStream = fs.createWriteStream(logPath, { flags: 'a' });
            this.logStream.write(`\n${'='.repeat(60)}\n` +
                `Runtime starting at ${new Date().toISOString()}\n` +
                `${'='.repeat(60)}\n`);
        }
        catch {
            // Non-fatal: logging to file is best-effort
        }
        // Kill any stale processes from a previous Electron session
        // that may still be holding their ports.
        this.killStaleProcess(cfg.runtimePort);
        this.killStaleProcess(9223); // Baileys WhatsApp bridge
        // Inject bundled Node.js path so skill bridges can find it
        const nodeBin = this.venv.getNodeBin();
        const npmBin = this.venv.getNpmBin();
        if (nodeBin)
            env.NLS_NODE_BIN = nodeBin;
        if (npmBin)
            env.NLS_NPM_BIN = npmBin;
        // Prevent fork-safety crashes when PyTorch MPS (Metal) is active.
        // Without this, any subprocess fork after MPS init creates zombie
        // processes that spin at 100% CPU on macOS Apple Silicon.
        if (process.platform === 'darwin') {
            env.OBJC_DISABLE_INITIALIZE_FORK_SAFETY = 'YES';
        }
        this.process = (0, child_process_1.spawn)(pythonBin, [
            '-m', 'uvicorn',
            'server.main:app',
            '--host', '127.0.0.1',
            '--port', String(cfg.runtimePort),
            '--log-level', 'info',
        ], {
            cwd: nlsRoot,
            stdio: ['pipe', 'pipe', 'pipe'],
            env: { ...process.env, ...env },
        });
        this.process.stdout?.on('data', (data) => {
            const message = data.toString();
            this.appendLog(message);
            this.broadcastLog('stdout', message);
        });
        this.process.stderr?.on('data', (data) => {
            const message = data.toString();
            this.appendLog(message);
            this.broadcastLog('stderr', message);
        });
        this.process.on('exit', (code) => {
            this.logShutdownEvent('runtime.child_exit', `exitCode=${code ?? 'null'}`);
            this._running = false;
            this.process = null;
            this.stopHealthCheck();
            // Exit code 75 = agent requested a graceful restart.
            if (code === 75) {
                this.broadcastLog('system', 'Runtime restart requested by agent, relaunching...');
                this.broadcastStatus();
                this.start().catch((err) => {
                    this._lastError = `Restart failed: ${err.message}`;
                    this.broadcastStatus();
                    this.broadcastLog('system', `Restart failed: ${err.message}`);
                });
                return;
            }
            if (code !== 0 && code !== null) {
                this._lastError = `Runtime exited with code ${code}`;
            }
            this.broadcastStatus();
            this.broadcastLog('system', `Agent runtime exited (code ${code})`);
        });
        this.process.on('error', (err) => {
            this._running = false;
            this._lastError = err.message;
            this.broadcastLog('system', `Runtime error: ${err.message}`);
            this.broadcastStatus();
        });
        // Wait for the runtime to become healthy
        await this.waitForHealth(cfg.runtimePort);
        this._running = true;
        this._startedAt = Date.now();
        this.startHealthCheck(cfg.runtimePort);
        this.startLeaseHeartbeat();
        this.broadcastStatus();
    }
    /**
     * Stop the Python agent runtime gracefully.
     */
    async stop() {
        this.logShutdownEvent('runtime.stop', this.captureCallerHint());
        this.stopLeaseHeartbeat();
        await this.releaseAllLeases();
        this.stopHealthCheck();
        if (this.process) {
            // On Windows, SIGTERM = TerminateProcess() which is instant and skips
            // Python shutdown hooks.  We must ask the runtime to shut down via
            // HTTP first and wait for the process to exit gracefully.  Only fall
            // back to forceful kill if it doesn't exit in time.
            let exited = false;
            const exitPromise = new Promise((resolve) => {
                this.process?.on('exit', () => {
                    exited = true;
                    resolve();
                });
            });
            try {
                const port = this.config.get().runtimePort;
                await fetch(`http://127.0.0.1:${port}/admin/shutdown`, {
                    method: 'POST',
                    signal: AbortSignal.timeout(5_000),
                });
                // Wait up to 10s for the process to exit after SIGINT
                await Promise.race([
                    exitPromise,
                    this.sleep(10_000),
                ]);
            }
            catch {
                // Runtime may already be down -- fall through to forceful kill
            }
            if (!exited && this.process) {
                this.logShutdownEvent('runtime.force_kill', `runtimePid=${this.process.pid ?? 'unknown'}`);
                console.log('Runtime did not exit gracefully, force killing...');
                if (process.platform === 'win32' && this.process.pid) {
                    try {
                        (0, child_process_1.execSync)(`taskkill /F /T /PID ${this.process.pid}`, { timeout: 5_000 });
                    }
                    catch { /* process may have already exited */ }
                }
                else {
                    this.process.kill('SIGTERM');
                }
                await Promise.race([exitPromise, this.sleep(3_000)]);
            }
            this.process = null;
        }
        // Final safety net: kill any bridge still holding port 9223
        this.killStaleProcess(9223);
        this._running = false;
        this._startedAt = 0;
        this._agentCount = 0;
        this._agentEnergy = {};
        // Close log file
        try {
            this.logStream?.end();
            this.logStream = null;
        }
        catch {
            // best-effort
        }
        this.broadcastStatus();
    }
    /**
     * Restart the runtime (e.g., after config change).
     */
    async restart() {
        this.logShutdownEvent('runtime.restart', this.captureCallerHint());
        await this.stop();
        await this.start();
    }
    /**
     * Get current runtime status.
     */
    getStatus() {
        return {
            running: this._running,
            port: this.config.get().runtimePort,
            pid: this.process?.pid ?? null,
            uptime: this._running ? Math.floor((Date.now() - this._startedAt) / 1000) : 0,
            agents: this._agentCount,
            error: this._lastError,
            agentEnergy: this._agentEnergy,
        };
    }
    /**
     * Get recent log lines.
     */
    getLogs(lines = 100) {
        return this.logBuffer.slice(-lines);
    }
    // ─── Health checking ──────────────────────────────────────────
    async waitForHealth(port) {
        const start = Date.now();
        let attempts = 0;
        while (Date.now() - start < STARTUP_TIMEOUT) {
            attempts++;
            const result = await this.checkHealth(port);
            if (result)
                return;
            // Check that the process hasn't crashed while we wait
            if (!this.process || this.process.exitCode !== null) {
                throw new Error(`Agent runtime process exited during startup (code ${this.process?.exitCode ?? 'unknown'}). ` +
                    'Check runtime.log for details.');
            }
            if (attempts % 5 === 0) {
                const elapsed = Math.round((Date.now() - start) / 1000);
                this.broadcastLog('system', `Still waiting for runtime to start (${elapsed}s elapsed)...`);
            }
            await this.sleep(2_000);
        }
        throw new Error(`Agent runtime failed to start within ${STARTUP_TIMEOUT / 1000}s. ` +
            'Check that Python dependencies are installed and the runtime is reachable.');
    }
    checkHealth(port) {
        return new Promise((resolve) => {
            const req = http.get(`http://127.0.0.1:${port}/health`, { timeout: 3_000 }, (res) => {
                if (res.statusCode === 200) {
                    let body = '';
                    res.on('data', (chunk) => { body += chunk; });
                    res.on('end', () => {
                        try {
                            const data = JSON.parse(body);
                            this._agentCount = data.agents_loaded ?? 0;
                            if (data.agent_energy && typeof data.agent_energy === 'object') {
                                this._agentEnergy = data.agent_energy;
                            }
                        }
                        catch {
                            // Non-critical
                        }
                        resolve(true);
                    });
                }
                else {
                    resolve(false);
                }
            });
            req.on('error', () => resolve(false));
            req.on('timeout', () => {
                req.destroy();
                resolve(false);
            });
        });
    }
    startHealthCheck(port) {
        this.healthTimer = setInterval(async () => {
            const healthy = await this.checkHealth(port);
            if (!healthy && this._running) {
                this._running = false;
                this._lastError = 'Health check failed';
                this.broadcastStatus();
                this.broadcastLog('system', 'Agent runtime health check failed');
            }
            else if (healthy && !this._running) {
                this._running = true;
                this._lastError = null;
                this.broadcastStatus();
            }
        }, HEALTH_CHECK_INTERVAL);
    }
    stopHealthCheck() {
        if (this.healthTimer) {
            clearInterval(this.healthTimer);
            this.healthTimer = null;
        }
    }
    // ─── Helpers ──────────────────────────────────────────────────
    killStaleProcess(port) {
        const isWin = process.platform === 'win32';
        if (isWin) {
            try {
                const out = (0, child_process_1.execSync)(`netstat -ano | findstr ":${port}" | findstr "LISTENING"`, { encoding: 'utf-8', timeout: 5_000 }).trim();
                for (const line of out.split('\n')) {
                    const pid = line.trim().split(/\s+/).pop();
                    if (pid && /^\d+$/.test(pid) && pid !== '0') {
                        this.logShutdownEvent('runtime.kill_stale', `port=${port} targetPid=${pid} method=taskkill`);
                        console.log(`Killing stale process on port ${port} (PID ${pid})`);
                        try {
                            (0, child_process_1.execSync)(`taskkill /f /pid ${pid}`, { timeout: 5_000 });
                        }
                        catch { /* ok */ }
                    }
                }
            }
            catch {
                // No stale process found -- expected on first launch
            }
        }
        else {
            // macOS / Linux: use lsof to find the PID holding the port
            try {
                const out = (0, child_process_1.execSync)(`lsof -ti tcp:${port}`, { encoding: 'utf-8', timeout: 5_000 }).trim();
                for (const pidStr of out.split('\n')) {
                    const pid = pidStr.trim();
                    if (pid && /^\d+$/.test(pid)) {
                        this.logShutdownEvent('runtime.kill_stale', `port=${port} targetPid=${pid} method=kill-9`);
                        console.log(`Killing stale process on port ${port} (PID ${pid})`);
                        try {
                            (0, child_process_1.execSync)(`kill -9 ${pid}`, { timeout: 5_000 });
                        }
                        catch { /* ok */ }
                    }
                }
            }
            catch {
                // No stale process found -- expected on first launch
            }
        }
    }
    logShutdownEvent(source, detail) {
        const runtimePid = this.process?.pid ?? 'none';
        const line = `${new Date().toISOString()} | WARNING | babo.electron | ` +
            `SHUTDOWN_TRACE ${source} electronPid=${process.pid} runtimePid=${runtimePid}` +
            `${detail ? ` ${detail}` : ''}\n`;
        console.log(line.trim());
        try {
            this.logStream?.write(line);
        }
        catch {
            // best-effort
        }
    }
    captureCallerHint() {
        const stack = new Error().stack;
        if (!stack)
            return '';
        const frames = stack
            .split('\n')
            .slice(2, 6)
            .map((line) => line.trim())
            .join(' <- ');
        return frames ? `caller=${frames.slice(0, 400)}` : '';
    }
    appendLog(message) {
        const lines = message.split('\n').filter(Boolean);
        this.logBuffer.push(...lines);
        if (this.logBuffer.length > this.maxLogLines) {
            this.logBuffer = this.logBuffer.slice(-this.maxLogLines);
        }
        try {
            this.logStream?.write(message);
        }
        catch {
            // best-effort
        }
    }
    broadcastLog(level, message) {
        for (const win of electron_1.BrowserWindow.getAllWindows()) {
            win.webContents.send('runtime:log', { level, message });
        }
    }
    broadcastStatus() {
        for (const win of electron_1.BrowserWindow.getAllWindows()) {
            win.webContents.send('runtime:status-changed', this.getStatus());
        }
    }
    sleep(ms) {
        return new Promise((resolve) => setTimeout(resolve, ms));
    }
    // ─── Device Lease management ─────────────────────────────────
    startLeaseHeartbeat() {
        this.leaseTimer = setInterval(() => {
            this.sendLeaseHeartbeats().catch(() => { });
        }, LEASE_HEARTBEAT_INTERVAL);
    }
    stopLeaseHeartbeat() {
        if (this.leaseTimer) {
            clearInterval(this.leaseTimer);
            this.leaseTimer = null;
        }
    }
    getDeviceId() {
        const os = require('os');
        const crypto = require('crypto');
        const raw = `${os.hostname()}-${os.platform()}-${os.arch()}-${os.userInfo().username}`;
        return crypto.createHash('sha256').update(raw).digest('hex').slice(0, 16);
    }
    async sendLeaseHeartbeats() {
        const cfg = this.config.get();
        const apiBase = config_manager_1.ConfigManager.nestjsApiBase(cfg.nestjsUrl);
        if (!apiBase)
            return;
        const deviceId = this.getDeviceId();
        const port = cfg.runtimePort;
        try {
            const res = await fetch(`http://127.0.0.1:${port}/agents`, { signal: AbortSignal.timeout(5_000) });
            if (!res.ok)
                return;
            const agents = await res.json();
            for (const agent of agents) {
                const agentId = agent.agent_id;
                if (!agentId)
                    continue;
                try {
                    await fetch(`${apiBase}/agents/${agentId}/lease/heartbeat`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ deviceId }),
                        signal: AbortSignal.timeout(5_000),
                    });
                    if (!this.activeLeaseAgents.includes(agentId)) {
                        // First heartbeat -- try to acquire lease
                        const acqRes = await fetch(`${apiBase}/agents/${agentId}/lease/acquire`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                deviceId,
                                deviceName: require('os').hostname(),
                            }),
                            signal: AbortSignal.timeout(5_000),
                        });
                        if (acqRes.ok) {
                            this.activeLeaseAgents.push(agentId);
                        }
                    }
                }
                catch {
                    // Non-critical: lease heartbeat failures are transient
                }
            }
        }
        catch {
            // Runtime not reachable -- skip this cycle
        }
    }
    async releaseAllLeases() {
        const cfg = this.config.get();
        const apiBase = config_manager_1.ConfigManager.nestjsApiBase(cfg.nestjsUrl);
        if (!apiBase || this.activeLeaseAgents.length === 0)
            return;
        for (const agentId of this.activeLeaseAgents) {
            try {
                await fetch(`${apiBase}/agents/${agentId}/lease/release`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({}),
                    signal: AbortSignal.timeout(5_000),
                });
            }
            catch {
                // Best-effort release
            }
        }
        this.activeLeaseAgents = [];
    }
}
exports.RuntimeManager = RuntimeManager;
//# sourceMappingURL=runtime-manager.js.map