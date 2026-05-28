"use strict";
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
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.UpdateManager = void 0;
const electron_updater_1 = require("electron-updater");
const electron_1 = require("electron");
const http = __importStar(require("http"));
const electron_log_1 = __importDefault(require("electron-log"));
// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------
const CHECK_DELAY_MS = 30_000; // first check 30 s after launch
const CHECK_INTERVAL_MS = 4 * 3600_000; // then every 4 hours
const IDLE_GRACE_MS = 30 * 60_000; // 30 min before unattended auto-update
const SAFE_POLL_MS = 60_000; // poll /admin/safe-to-update every 60 s
const FORCE_UPDATE_MS = 6 * 3600_000; // hard cap: force after 6 h
// ---------------------------------------------------------------------------
// UpdateManager
// ---------------------------------------------------------------------------
class UpdateManager {
    runtime;
    config;
    _state = 'idle';
    _version = null;
    _releaseNotes = null;
    _releaseDate = null;
    _progress = null;
    _error = null;
    _snoozeUntil = null;
    checkTimer = null;
    idleTimer = null;
    safePollTimer = null;
    forceTimer = null;
    lastUserActivity = Date.now();
    mainWindow = null;
    constructor(runtime, config) {
        this.runtime = runtime;
        this.config = config;
    }
    // ─── Public API ─────────────────────────────────────────────────
    /**
     * Wire up autoUpdater events and start the periodic check scheduler.
     * Call once after the main window is created.
     */
    initialize(win) {
        this.mainWindow = win;
        electron_updater_1.autoUpdater.logger = electron_log_1.default;
        electron_updater_1.autoUpdater.logger.transports.file.level = 'debug';
        const token = process.env.GH_TOKEN || process.env.GITHUB_TOKEN;
        electron_updater_1.autoUpdater.setFeedURL({
            provider: 'github',
            owner: process.env.GH_RELEASE_OWNER || 'umbecanessa',
            repo: process.env.GH_RELEASE_REPO || 'babo',
            private: Boolean(token),
            ...(token ? { token } : {}),
        });
        electron_updater_1.autoUpdater.autoDownload = false;
        electron_updater_1.autoUpdater.autoInstallOnAppQuit = true;
        electron_updater_1.autoUpdater.allowPrerelease = false;
        electron_log_1.default.info('[UpdateManager] initialized — current version', electron_1.app.getVersion());
        this.bindAutoUpdaterEvents();
        this.trackUserActivity(win);
        // First check after a short delay, then periodic
        electron_log_1.default.info('[UpdateManager] scheduling first check in', CHECK_DELAY_MS, 'ms');
        setTimeout(() => this.checkForUpdates(), CHECK_DELAY_MS);
        this.checkTimer = setInterval(() => this.checkForUpdates(), CHECK_INTERVAL_MS);
    }
    async checkForUpdates() {
        if (this._snoozeUntil && Date.now() < this._snoozeUntil) {
            electron_log_1.default.info('[UpdateManager] snoozed — skipping check');
            return this.getStatus();
        }
        try {
            this.setState('checking');
            electron_log_1.default.info('[UpdateManager] checking for updates …');
            const result = await electron_updater_1.autoUpdater.checkForUpdates();
            electron_log_1.default.info('[UpdateManager] check result:', JSON.stringify(result?.updateInfo?.version));
        }
        catch (err) {
            electron_log_1.default.error('[UpdateManager] check error:', err);
            this.setState('error');
            this._error = err.message ?? 'Update check failed';
            this.broadcast('update:error', { message: this._error });
        }
        return this.getStatus();
    }
    async downloadUpdate() {
        try {
            this.setState('downloading');
            await electron_updater_1.autoUpdater.downloadUpdate();
        }
        catch (err) {
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
    _isUpdating = false;
    get isUpdating() {
        return this._isUpdating;
    }
    async installUpdate() {
        this.setState('installing');
        this.broadcast('update:installing', {});
        this.clearTimers();
        try {
            await this.runtime.stop();
        }
        catch {
            // Best-effort -- proceed with install even if runtime stop fails
        }
        this._isUpdating = true;
        electron_updater_1.autoUpdater.quitAndInstall(false, true);
    }
    snooze(durationMs) {
        this._snoozeUntil = Date.now() + durationMs;
        this.cancelUnattendedInstall();
        return this.getStatus();
    }
    getStatus() {
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
    dispose() {
        this.clearTimers();
    }
    // ─── Auto-updater event wiring ─────────────────────────────────
    bindAutoUpdaterEvents() {
        electron_updater_1.autoUpdater.on('update-available', (info) => {
            electron_log_1.default.info('[UpdateManager] update AVAILABLE — version', info.version);
            this.setState('available');
            this._version = info.version;
            this._releaseNotes = this.extractReleaseNotes(info);
            this._releaseDate = info.releaseDate ?? null;
            this.broadcast('update:available', {
                version: this._version,
                releaseNotes: this._releaseNotes,
                releaseDate: this._releaseDate,
            });
            this.scheduleUnattendedInstall();
        });
        electron_updater_1.autoUpdater.on('update-not-available', (info) => {
            electron_log_1.default.info('[UpdateManager] update NOT available — latest is', info.version);
            this.setState('idle');
            this.broadcast('update:not-available', { version: info.version });
        });
        electron_updater_1.autoUpdater.on('download-progress', (progress) => {
            this._progress = progress;
            this.broadcast('update:download-progress', { ...progress });
        });
        electron_updater_1.autoUpdater.on('update-downloaded', (info) => {
            this.setState('downloaded');
            this._version = info.version;
            this.broadcast('update:downloaded', { version: info.version });
        });
        electron_updater_1.autoUpdater.on('error', (err) => {
            electron_log_1.default.error('[UpdateManager] autoUpdater error:', err.message, err.stack);
            this.setState('error');
            this._error = err.message;
            this.broadcast('update:error', { message: err.message });
        });
    }
    // ─── User-activity tracking ────────────────────────────────────
    trackUserActivity(win) {
        const touch = () => { this.lastUserActivity = Date.now(); };
        win.on('focus', touch);
        win.on('move', touch);
        win.on('resize', touch);
        win.webContents.on('before-input-event', touch);
    }
    get userIsIdle() {
        return Date.now() - this.lastUserActivity > IDLE_GRACE_MS;
    }
    // ─── Unattended auto-install ───────────────────────────────────
    /**
     * When an update is available, wait for the user to go idle, then
     * poll the runtime for pipeline safety before auto-installing.
     */
    scheduleUnattendedInstall() {
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
    beginSafetyPolling() {
        if (this.safePollTimer)
            return; // already polling
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
    async downloadAndInstallSilently() {
        try {
            this.setState('downloading');
            await electron_updater_1.autoUpdater.downloadUpdate();
            // update-downloaded event fires -> state becomes 'downloaded'
            // Small delay to let the event propagate
            await this.sleep(2_000);
            await this.installUpdate();
        }
        catch (err) {
            this.setState('error');
            this._error = err.message ?? 'Silent update failed';
            this.broadcast('update:error', { message: this._error });
        }
    }
    performUnattendedInstall() {
        this.cancelUnattendedInstall();
        this.downloadAndInstallSilently();
    }
    cancelUnattendedInstall() {
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
    isRuntimeSafeToUpdate() {
        const port = this.config.get().runtimePort;
        return new Promise((resolve) => {
            const req = http.get(`http://127.0.0.1:${port}/admin/safe-to-update`, { timeout: 5_000 }, (res) => {
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
                    }
                    catch {
                        resolve(true);
                    }
                });
            });
            req.on('error', () => resolve(true)); // runtime down -> safe
            req.on('timeout', () => { req.destroy(); resolve(true); });
        });
    }
    // ─── Helpers ───────────────────────────────────────────────────
    setState(state) {
        this._state = state;
        if (state !== 'error')
            this._error = null;
        if (state !== 'downloading')
            this._progress = null;
    }
    broadcast(channel, data) {
        for (const win of electron_1.BrowserWindow.getAllWindows()) {
            win.webContents.send(channel, data);
        }
    }
    extractReleaseNotes(info) {
        if (!info.releaseNotes)
            return null;
        if (typeof info.releaseNotes === 'string')
            return info.releaseNotes;
        if (Array.isArray(info.releaseNotes)) {
            return info.releaseNotes.map((n) => n.note).join('\n');
        }
        return null;
    }
    clearTimers() {
        if (this.checkTimer) {
            clearInterval(this.checkTimer);
            this.checkTimer = null;
        }
        this.cancelUnattendedInstall();
    }
    sleep(ms) {
        return new Promise((resolve) => setTimeout(resolve, ms));
    }
}
exports.UpdateManager = UpdateManager;
//# sourceMappingURL=update-manager.js.map