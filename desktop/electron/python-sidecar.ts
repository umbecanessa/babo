/**
 * @deprecated Replaced by RuntimeManager (runtime-manager.ts).
 * Kept for reference only -- not imported by main.ts.
 *
 * The old PythonSidecar ran `nls.engine.serve_local` for local
 * inference with a locally-loaded model.  The new RuntimeManager
 * runs the full `server.main:app` in remote mode instead.
 */

import { ChildProcess, spawn } from 'child_process';
import { BrowserWindow } from 'electron';
import * as path from 'path';
import * as http from 'http';

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

export class PythonSidecar {
  private process: ChildProcess | null = null;
  private port: number = DEFAULT_PORT;
  private model: string | null = null;
  private healthTimer: ReturnType<typeof setInterval> | null = null;
  private _running = false;

  /**
   * Start the Python sidecar process.
   */
  async start(model?: string): Promise<void> {
    if (this.process) {
      return; // Already running
    }

    this.model = model || DEFAULT_MODEL;
    this.port = DEFAULT_PORT;

    // Determine the NLS project root (two levels up from dist-electron/)
    const nlsRoot = path.resolve(__dirname, '..', '..');

    // Spawn the Python process
    this.process = spawn(
      'python',
      [
        '-m',
        'nls.engine.serve_local',
        '--port',
        String(this.port),
        '--model',
        this.model,
      ],
      {
        cwd: nlsRoot,
        stdio: ['pipe', 'pipe', 'pipe'],
        env: {
          ...process.env,
          PYTHONUNBUFFERED: '1',
        },
      },
    );

    // Forward stdout/stderr to renderer as log events
    this.process.stdout?.on('data', (data: Buffer) => {
      const message = data.toString();
      this.broadcastLog('stdout', message);
    });

    this.process.stderr?.on('data', (data: Buffer) => {
      const message = data.toString();
      this.broadcastLog('stderr', message);
    });

    this.process.on('exit', (code) => {
      this._running = false;
      this.process = null;
      this.stopHealthCheck();
      this.broadcastStatus();
      this.broadcastLog(
        'system',
        `Python sidecar exited with code ${code}`,
      );
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
  async stop(): Promise<void> {
    this.stopHealthCheck();

    if (this.process) {
      this.process.kill('SIGTERM');

      // Give it 5 seconds to shut down gracefully
      await new Promise<void>((resolve) => {
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
  getStatus(): {
    running: boolean;
    port: number;
    pid: number | null;
    model: string | null;
  } {
    return {
      running: this._running,
      port: this.port,
      pid: this.process?.pid ?? null,
      model: this.model,
    };
  }

  // ─── Health checking ────────────────────────────────────────────────

  private async waitForHealth(): Promise<void> {
    const start = Date.now();

    while (Date.now() - start < STARTUP_TIMEOUT) {
      if (await this.checkHealth()) {
        return;
      }
      await this.sleep(2_000);
    }

    throw new Error(
      `Python sidecar failed to start within ${STARTUP_TIMEOUT / 1000}s`,
    );
  }

  private checkHealth(): Promise<boolean> {
    return new Promise((resolve) => {
      const req = http.get(
        `http://localhost:${this.port}/health`,
        { timeout: 3_000 },
        (res) => {
          resolve(res.statusCode === 200);
        },
      );
      req.on('error', () => resolve(false));
      req.on('timeout', () => {
        req.destroy();
        resolve(false);
      });
    });
  }

  private startHealthCheck(): void {
    this.healthTimer = setInterval(async () => {
      const healthy = await this.checkHealth();
      if (!healthy && this._running) {
        this._running = false;
        this.broadcastStatus();
        this.broadcastLog(
          'system',
          'Python sidecar health check failed',
        );
      }
    }, HEALTH_CHECK_INTERVAL);
  }

  private stopHealthCheck(): void {
    if (this.healthTimer) {
      clearInterval(this.healthTimer);
      this.healthTimer = null;
    }
  }

  // ─── Helpers ────────────────────────────────────────────────────────

  private broadcastLog(level: string, message: string): void {
    for (const win of BrowserWindow.getAllWindows()) {
      win.webContents.send('sidecar:log', { level, message });
    }
  }

  private broadcastStatus(): void {
    for (const win of BrowserWindow.getAllWindows()) {
      win.webContents.send('sidecar:status-changed', this.getStatus());
    }
  }

  private sleep(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }
}
