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

import { ChildProcess, execSync, spawn } from 'child_process';
import { app, BrowserWindow } from 'electron';
import * as fs from 'fs';
import * as http from 'http';
import * as path from 'path';

import { ConfigManager } from './config-manager';
import { VenvManager } from './venv-manager';

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const HEALTH_CHECK_INTERVAL = 5_000;
const STARTUP_TIMEOUT = 180_000;
const LEASE_HEARTBEAT_INTERVAL = 60_000;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface AgentEnergyInfo {
  energy_level: number;
  agent_status: string;
}

export interface RuntimeStatus {
  running: boolean;
  port: number;
  pid: number | null;
  uptime: number;
  agents: number;
  error: string | null;
  agentEnergy: Record<string, AgentEnergyInfo>;
}

// ---------------------------------------------------------------------------
// RuntimeManager
// ---------------------------------------------------------------------------

export class RuntimeManager {
  private process: ChildProcess | null = null;
  private healthTimer: ReturnType<typeof setInterval> | null = null;
  private leaseTimer: ReturnType<typeof setInterval> | null = null;
  private _running = false;
  private _startedAt = 0;
  private _agentCount = 0;
  private _agentEnergy: Record<string, AgentEnergyInfo> = {};
  private _lastError: string | null = null;
  private logBuffer: string[] = [];
  private maxLogLines = 500;
  private logStream: fs.WriteStream | null = null;
  private activeLeaseAgents: string[] = [];

  constructor(
    private config: ConfigManager,
    private venv: VenvManager,
  ) {}

  /**
   * Start the Python agent runtime.
   */
  async start(): Promise<void> {
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
    const logPath = path.join(app.getPath('userData'), 'runtime.log');
    try {
      this.logStream = fs.createWriteStream(logPath, { flags: 'a' });
      this.logStream.write(
        `\n${'='.repeat(60)}\n` +
        `Runtime starting at ${new Date().toISOString()}\n` +
        `${'='.repeat(60)}\n`,
      );
    } catch {
      // Non-fatal: logging to file is best-effort
    }

    // Kill any stale processes from a previous Electron session
    // that may still be holding their ports.
    this.killStaleProcess(cfg.runtimePort);
    this.killStaleProcess(9223); // Baileys WhatsApp bridge

    // Inject bundled Node.js path so skill bridges can find it
    const nodeBin = this.venv.getNodeBin();
    const npmBin = this.venv.getNpmBin();
    if (nodeBin) env.NLS_NODE_BIN = nodeBin;
    if (npmBin) env.NLS_NPM_BIN = npmBin;

    // Prevent fork-safety crashes when PyTorch MPS (Metal) is active.
    // Without this, any subprocess fork after MPS init creates zombie
    // processes that spin at 100% CPU on macOS Apple Silicon.
    if (process.platform === 'darwin') {
      env.OBJC_DISABLE_INITIALIZE_FORK_SAFETY = 'YES';
    }

    this.process = spawn(
      pythonBin,
      [
        '-m', 'uvicorn',
        'server.main:app',
        '--host', '127.0.0.1',
        '--port', String(cfg.runtimePort),
        '--log-level', 'info',
      ],
      {
        cwd: nlsRoot,
        stdio: ['pipe', 'pipe', 'pipe'],
        env: { ...process.env, ...env },
      },
    );

    this.process.stdout?.on('data', (data: Buffer) => {
      const message = data.toString();
      this.appendLog(message);
      this.broadcastLog('stdout', message);
    });

    this.process.stderr?.on('data', (data: Buffer) => {
      const message = data.toString();
      this.appendLog(message);
      this.broadcastLog('stderr', message);
    });

    this.process.on('exit', (code) => {
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
  async stop(): Promise<void> {
    this.stopLeaseHeartbeat();
    await this.releaseAllLeases();
    this.stopHealthCheck();

    if (this.process) {
      // On Windows, SIGTERM = TerminateProcess() which is instant and skips
      // Python shutdown hooks.  We must ask the runtime to shut down via
      // HTTP first and wait for the process to exit gracefully.  Only fall
      // back to forceful kill if it doesn't exit in time.
      let exited = false;

      const exitPromise = new Promise<void>((resolve) => {
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
      } catch {
        // Runtime may already be down -- fall through to forceful kill
      }

      if (!exited && this.process) {
        console.log('Runtime did not exit gracefully, force killing...');
        if (process.platform === 'win32' && this.process.pid) {
          try {
            execSync(`taskkill /F /T /PID ${this.process.pid}`, { timeout: 5_000 });
          } catch { /* process may have already exited */ }
        } else {
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
    } catch {
      // best-effort
    }
    this.broadcastStatus();
  }

  /**
   * Restart the runtime (e.g., after config change).
   */
  async restart(): Promise<void> {
    await this.stop();
    await this.start();
  }

  /**
   * Get current runtime status.
   */
  getStatus(): RuntimeStatus {
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
  getLogs(lines = 100): string[] {
    return this.logBuffer.slice(-lines);
  }

  // ─── Health checking ──────────────────────────────────────────

  private async waitForHealth(port: number): Promise<void> {
    const start = Date.now();
    let attempts = 0;

    while (Date.now() - start < STARTUP_TIMEOUT) {
      attempts++;
      const result = await this.checkHealth(port);
      if (result) return;

      // Check that the process hasn't crashed while we wait
      if (!this.process || this.process.exitCode !== null) {
        throw new Error(
          `Agent runtime process exited during startup (code ${this.process?.exitCode ?? 'unknown'}). ` +
          'Check runtime.log for details.',
        );
      }

      if (attempts % 5 === 0) {
        const elapsed = Math.round((Date.now() - start) / 1000);
        this.broadcastLog('system', `Still waiting for runtime to start (${elapsed}s elapsed)...`);
      }
      await this.sleep(2_000);
    }

    throw new Error(
      `Agent runtime failed to start within ${STARTUP_TIMEOUT / 1000}s. ` +
      'Check that Python dependencies are installed and the runtime is reachable.',
    );
  }

  private checkHealth(port: number): Promise<boolean> {
    return new Promise((resolve) => {
      const req = http.get(
        `http://127.0.0.1:${port}/health`,
        { timeout: 3_000 },
        (res) => {
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
              } catch {
                // Non-critical
              }
              resolve(true);
            });
          } else {
            resolve(false);
          }
        },
      );
      req.on('error', () => resolve(false));
      req.on('timeout', () => {
        req.destroy();
        resolve(false);
      });
    });
  }

  private startHealthCheck(port: number): void {
    this.healthTimer = setInterval(async () => {
      const healthy = await this.checkHealth(port);
      if (!healthy && this._running) {
        this._running = false;
        this._lastError = 'Health check failed';
        this.broadcastStatus();
        this.broadcastLog('system', 'Agent runtime health check failed');
      } else if (healthy && !this._running) {
        this._running = true;
        this._lastError = null;
        this.broadcastStatus();
      }
    }, HEALTH_CHECK_INTERVAL);
  }

  private stopHealthCheck(): void {
    if (this.healthTimer) {
      clearInterval(this.healthTimer);
      this.healthTimer = null;
    }
  }

  // ─── Helpers ──────────────────────────────────────────────────

  private killStaleProcess(port: number): void {
    const isWin = process.platform === 'win32';

    if (isWin) {
      try {
        const out = execSync(
          `netstat -ano | findstr ":${port}" | findstr "LISTENING"`,
          { encoding: 'utf-8', timeout: 5_000 },
        ).trim();
        for (const line of out.split('\n')) {
          const pid = line.trim().split(/\s+/).pop();
          if (pid && /^\d+$/.test(pid) && pid !== '0') {
            console.log(`Killing stale process on port ${port} (PID ${pid})`);
            try { execSync(`taskkill /f /pid ${pid}`, { timeout: 5_000 }); } catch { /* ok */ }
          }
        }
      } catch {
        // No stale process found -- expected on first launch
      }
    } else {
      // macOS / Linux: use lsof to find the PID holding the port
      try {
        const out = execSync(
          `lsof -ti tcp:${port}`,
          { encoding: 'utf-8', timeout: 5_000 },
        ).trim();
        for (const pidStr of out.split('\n')) {
          const pid = pidStr.trim();
          if (pid && /^\d+$/.test(pid)) {
            console.log(`Killing stale process on port ${port} (PID ${pid})`);
            try { execSync(`kill -9 ${pid}`, { timeout: 5_000 }); } catch { /* ok */ }
          }
        }
      } catch {
        // No stale process found -- expected on first launch
      }
    }
  }

  private appendLog(message: string): void {
    const lines = message.split('\n').filter(Boolean);
    this.logBuffer.push(...lines);
    if (this.logBuffer.length > this.maxLogLines) {
      this.logBuffer = this.logBuffer.slice(-this.maxLogLines);
    }
    try {
      this.logStream?.write(message);
    } catch {
      // best-effort
    }
  }

  private broadcastLog(level: string, message: string): void {
    for (const win of BrowserWindow.getAllWindows()) {
      win.webContents.send('runtime:log', { level, message });
    }
  }

  private broadcastStatus(): void {
    for (const win of BrowserWindow.getAllWindows()) {
      win.webContents.send('runtime:status-changed', this.getStatus());
    }
  }

  private sleep(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  // ─── Device Lease management ─────────────────────────────────

  private startLeaseHeartbeat(): void {
    this.leaseTimer = setInterval(() => {
      this.sendLeaseHeartbeats().catch(() => {});
    }, LEASE_HEARTBEAT_INTERVAL);
  }

  private stopLeaseHeartbeat(): void {
    if (this.leaseTimer) {
      clearInterval(this.leaseTimer);
      this.leaseTimer = null;
    }
  }

  private getDeviceId(): string {
    const os = require('os');
    const crypto = require('crypto');
    const raw = `${os.hostname()}-${os.platform()}-${os.arch()}-${os.userInfo().username}`;
    return crypto.createHash('sha256').update(raw).digest('hex').slice(0, 16);
  }

  private async sendLeaseHeartbeats(): Promise<void> {
    const cfg = this.config.get();
    const apiBase = ConfigManager.nestjsApiBase(cfg.nestjsUrl);
    if (!apiBase) return;

    const deviceId = this.getDeviceId();
    const port = cfg.runtimePort;

    try {
      const res = await fetch(`http://127.0.0.1:${port}/agents`, { signal: AbortSignal.timeout(5_000) });
      if (!res.ok) return;
      const agents: any[] = await res.json() as any[];

      for (const agent of agents) {
        const agentId = agent.agent_id;
        if (!agentId) continue;

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
        } catch {
          // Non-critical: lease heartbeat failures are transient
        }
      }
    } catch {
      // Runtime not reachable -- skip this cycle
    }
  }

  private async releaseAllLeases(): Promise<void> {
    const cfg = this.config.get();
    const apiBase = ConfigManager.nestjsApiBase(cfg.nestjsUrl);
    if (!apiBase || this.activeLeaseAgents.length === 0) return;

    for (const agentId of this.activeLeaseAgents) {
      try {
        await fetch(`${apiBase}/agents/${agentId}/lease/release`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({}),
          signal: AbortSignal.timeout(5_000),
        });
      } catch {
        // Best-effort release
      }
    }
    this.activeLeaseAgents = [];
  }
}
