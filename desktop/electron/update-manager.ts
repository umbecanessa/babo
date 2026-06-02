/**
 * NLS Desktop App -- Auto-Update Manager
 *
 * Wraps electron-updater to provide:
 *   - Periodic update checks (on launch + every 4 hours)
 *   - Event broadcasting to the renderer via IPC
 *   - Agent-aware unattended auto-install (checks /admin/safe-to-update)
 *   - Graceful runtime shutdown before quit-and-install
 *   - Snooze support (1 h / 4 h / tomorrow)
 */

import { autoUpdater, UpdateInfo, ProgressInfo } from 'electron-updater';
import { BrowserWindow, app } from 'electron';
import * as http from 'http';
import log from 'electron-log';

import { RuntimeManager } from './runtime-manager';
import { ConfigManager } from './config-manager';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const CHECK_DELAY_MS = 30_000;          // first check 30 s after launch
const CHECK_INTERVAL_MS = 4 * 3600_000; // then every 4 hours
const IDLE_GRACE_MS = 30 * 60_000;      // 30 min before unattended auto-update
const SAFE_POLL_MS = 60_000;            // poll /admin/safe-to-update every 60 s
const FORCE_UPDATE_MS = 6 * 3600_000;   // hard cap: force after 6 h

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type UpdateState =
  | 'idle'
  | 'checking'
  | 'available'
  | 'downloading'
  | 'downloaded'
  | 'installing'
  | 'error';

export interface UpdateStatus {
  state: UpdateState;
  version: string | null;
  releaseNotes: string | null;
  releaseDate: string | null;
  progress: ProgressInfo | null;
  error: string | null;
  snoozeUntil: number | null;
}

// ---------------------------------------------------------------------------
// UpdateManager
// ---------------------------------------------------------------------------

export class UpdateManager {
  private _state: UpdateState = 'idle';
  private _version: string | null = null;
  private _releaseNotes: string | null = null;
  private _releaseDate: string | null = null;
  private _progress: ProgressInfo | null = null;
  private _error: string | null = null;
  private _snoozeUntil: number | null = null;

  private checkTimer: ReturnType<typeof setInterval> | null = null;
  private idleTimer: ReturnType<typeof setTimeout> | null = null;
  private safePollTimer: ReturnType<typeof setInterval> | null = null;
  private forceTimer: ReturnType<typeof setTimeout> | null = null;

  private lastUserActivity = Date.now();
  private mainWindow: BrowserWindow | null = null;

  constructor(
    private runtime: RuntimeManager,
    private config: ConfigManager,
  ) {}

  // ─── Public API ─────────────────────────────────────────────────

  /**
   * Wire up autoUpdater events and start the periodic check scheduler.
   * Call once after the main window is created.
   */
  initialize(win: BrowserWindow): void {
    this.mainWindow = win;

    if (!app.isPackaged) {
      log.info('[UpdateManager] skipping auto-update (app is not packaged)');
      return;
    }

    autoUpdater.logger = log;
    (autoUpdater.logger as any).transports.file.level = 'debug';

    const token = process.env.GH_TOKEN || process.env.GITHUB_TOKEN;
    autoUpdater.setFeedURL({
      provider: 'github',
      owner: process.env.GH_RELEASE_OWNER || 'umbecanessa',
      repo: process.env.GH_RELEASE_REPO || 'babo',
      private: Boolean(token),
      ...(token ? { token } : {}),
    } as any);

    autoUpdater.autoDownload = false;
    autoUpdater.autoInstallOnAppQuit = true;
    autoUpdater.allowPrerelease = false;

    log.info('[UpdateManager] initialized — current version', app.getVersion());

    this.bindAutoUpdaterEvents();
    this.trackUserActivity(win);

    // First check after a short delay, then periodic
    log.info('[UpdateManager] scheduling first check in', CHECK_DELAY_MS, 'ms');
    setTimeout(() => this.checkForUpdates(), CHECK_DELAY_MS);
    this.checkTimer = setInterval(() => this.checkForUpdates(), CHECK_INTERVAL_MS);
  }

  async checkForUpdates(): Promise<UpdateStatus> {
    if (this._snoozeUntil && Date.now() < this._snoozeUntil) {
      log.info('[UpdateManager] snoozed — skipping check');
      return this.getStatus();
    }

    try {
      this.setState('checking');
      log.info('[UpdateManager] checking for updates …');
      const result = await autoUpdater.checkForUpdates();
      log.info('[UpdateManager] check result:', JSON.stringify(result?.updateInfo?.version));
    } catch (err: any) {
      log.error('[UpdateManager] check error:', err);
      this.setState('error');
      this._error = err.message ?? 'Update check failed';
      this.broadcast('update:error', { message: this._error });
    }
    return this.getStatus();
  }

  async downloadUpdate(): Promise<void> {
    try {
      this.setState('downloading');
      await autoUpdater.downloadUpdate();
    } catch (err: any) {
      this.setState('error');
      this._error = err.message ?? 'Download failed';
      this.broadcast('update:error', { message: this._error });
    }
  }

  /**
   * Flag set just before quitAndInstall so the before-quit handler
   * in main.ts can skip event.preventDefault() and let autoUpdater
   * complete the update-and-relaunch cycle (critical on macOS).
   */
  private _isUpdating = false;

  get isUpdating(): boolean {
    return this._isUpdating;
  }

  async installUpdate(): Promise<void> {
    this.setState('installing');
    this.broadcast('update:installing', {});

    this.clearTimers();

    try {
      log.warn('[UpdateManager] SHUTDOWN_TRACE update.installUpdate calling runtime.stop()');
      await this.runtime.stop();
    } catch {
      // Best-effort -- proceed with install even if runtime stop fails
    }

    this._isUpdating = true;
    autoUpdater.quitAndInstall(false, true);
  }

  snooze(durationMs: number): UpdateStatus {
    this._snoozeUntil = Date.now() + durationMs;
    this.cancelUnattendedInstall();
    return this.getStatus();
  }

  getStatus(): UpdateStatus {
    return {
      state: this._state,
      version: this._version,
      releaseNotes: this._releaseNotes,
      releaseDate: this._releaseDate,
      progress: this._progress,
      error: this._error,
      snoozeUntil: this._snoozeUntil,
    };
  }

  dispose(): void {
    this.clearTimers();
  }

  // ─── Auto-updater event wiring ─────────────────────────────────

  private bindAutoUpdaterEvents(): void {
    autoUpdater.on('update-available', (info: UpdateInfo) => {
      log.info('[UpdateManager] update AVAILABLE — version', info.version);
      this.setState('available');
      this._version = info.version;
      this._releaseNotes = this.extractReleaseNotes(info);
      this._releaseDate = (info as any).releaseDate ?? null;

      this.broadcast('update:available', {
        version: this._version,
        releaseNotes: this._releaseNotes,
        releaseDate: this._releaseDate,
      });

      this.scheduleUnattendedInstall();
    });

    autoUpdater.on('update-not-available', (info: UpdateInfo) => {
      log.info('[UpdateManager] update NOT available — latest is', info.version);
      this.setState('idle');
      this.broadcast('update:not-available', { version: info.version });
    });

    autoUpdater.on('download-progress', (progress: ProgressInfo) => {
      this._progress = progress;
      this.broadcast('update:download-progress', { ...progress });
    });

    autoUpdater.on('update-downloaded', (info: UpdateInfo) => {
      this.setState('downloaded');
      this._version = info.version;
      this.broadcast('update:downloaded', { version: info.version });
    });

    autoUpdater.on('error', (err: Error) => {
      log.error('[UpdateManager] autoUpdater error:', err.message, err.stack);
      this.setState('error');
      this._error = err.message;
      this.broadcast('update:error', { message: err.message });
    });
  }

  // ─── User-activity tracking ────────────────────────────────────

  private trackUserActivity(win: BrowserWindow): void {
    const touch = () => { this.lastUserActivity = Date.now(); };
    win.on('focus', touch);
    win.on('move', touch);
    win.on('resize', touch);
    win.webContents.on('before-input-event', touch);
  }

  private get userIsIdle(): boolean {
    return Date.now() - this.lastUserActivity > IDLE_GRACE_MS;
  }

  // ─── Unattended auto-install ───────────────────────────────────

  /**
   * When an update is available, wait for the user to go idle, then
   * poll the runtime for pipeline safety before auto-installing.
   */
  private scheduleUnattendedInstall(): void {
    this.cancelUnattendedInstall();

    // Set a hard ceiling so headless machines don't stay stale forever
    this.forceTimer = setTimeout(() => {
      this.performUnattendedInstall();
    }, FORCE_UPDATE_MS);

    this.idleTimer = setInterval(() => {
      if (this.userIsIdle && this._state === 'available') {
        this.beginSafetyPolling();
      }
    }, 60_000);
  }

  private beginSafetyPolling(): void {
    if (this.safePollTimer) return; // already polling

    // Clear the idle check -- we're now in safety-polling mode
    if (this.idleTimer) {
      clearInterval(this.idleTimer);
      this.idleTimer = null;
    }

    const poll = async () => {
      const safe = await this.isRuntimeSafeToUpdate();
      if (safe) {
        this.cancelUnattendedInstall();
        await this.downloadAndInstallSilently();
      }
    };

    poll(); // immediate first attempt
    this.safePollTimer = setInterval(poll, SAFE_POLL_MS);
  }

  private async downloadAndInstallSilently(): Promise<void> {
    try {
      this.setState('downloading');
      await autoUpdater.downloadUpdate();
      // update-downloaded event fires -> state becomes 'downloaded'
      // Small delay to let the event propagate
      await this.sleep(2_000);
      await this.installUpdate();
    } catch (err: any) {
      this.setState('error');
      this._error = err.message ?? 'Silent update failed';
      this.broadcast('update:error', { message: this._error });
    }
  }

  private performUnattendedInstall(): void {
    this.cancelUnattendedInstall();
    this.downloadAndInstallSilently();
  }

  private cancelUnattendedInstall(): void {
    if (this.idleTimer) {
      clearInterval(this.idleTimer);
      this.idleTimer = null;
    }
    if (this.safePollTimer) {
      clearInterval(this.safePollTimer);
      this.safePollTimer = null;
    }
    if (this.forceTimer) {
      clearTimeout(this.forceTimer);
      this.forceTimer = null;
    }
  }

  // ─── Runtime safety check ──────────────────────────────────────

  private isRuntimeSafeToUpdate(): Promise<boolean> {
    const port = this.config.get().runtimePort;

    return new Promise((resolve) => {
      const req = http.get(
        `http://127.0.0.1:${port}/admin/safe-to-update`,
        { timeout: 5_000 },
        (res) => {
          if (res.statusCode !== 200) {
            resolve(true); // runtime not reachable / no endpoint -> safe
            return;
          }
          let body = '';
          res.on('data', (chunk) => { body += chunk; });
          res.on('end', () => {
            try {
              const data = JSON.parse(body);
              resolve(data.safe === true);
            } catch {
              resolve(true);
            }
          });
        },
      );
      req.on('error', () => resolve(true)); // runtime down -> safe
      req.on('timeout', () => { req.destroy(); resolve(true); });
    });
  }

  // ─── Helpers ───────────────────────────────────────────────────

  private setState(state: UpdateState): void {
    this._state = state;
    if (state !== 'error') this._error = null;
    if (state !== 'downloading') this._progress = null;
  }

  private broadcast(channel: string, data: Record<string, unknown>): void {
    for (const win of BrowserWindow.getAllWindows()) {
      win.webContents.send(channel, data);
    }
  }

  private extractReleaseNotes(info: UpdateInfo): string | null {
    if (!info.releaseNotes) return null;
    if (typeof info.releaseNotes === 'string') return info.releaseNotes;
    if (Array.isArray(info.releaseNotes)) {
      return info.releaseNotes.map((n) => n.note).join('\n');
    }
    return null;
  }

  private clearTimers(): void {
    if (this.checkTimer) { clearInterval(this.checkTimer); this.checkTimer = null; }
    this.cancelUnattendedInstall();
  }

  private sleep(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }
}
