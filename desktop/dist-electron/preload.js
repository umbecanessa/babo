"use strict";
/**
 * NLS Desktop App -- Preload Script
 *
 * Exposes safe, sandboxed APIs from the Electron main process to the
 * Angular renderer via contextBridge.  The renderer accesses these
 * through `window.nls`.
 *
 * This is the ONLY bridge between Node.js/Electron and the browser context.
 * All capabilities are permission-gated on the main-process side.
 */
Object.defineProperty(exports, "__esModule", { value: true });
const electron_1 = require("electron");
function readBootConfig() {
    try {
        return electron_1.ipcRenderer.sendSync('config:boot');
    }
    catch {
        return null;
    }
}
const bootConfig = readBootConfig();
// ---------------------------------------------------------------------------
// Valid event channels (main -> renderer)
// ---------------------------------------------------------------------------
const VALID_CHANNELS = [
    'runtime:status-changed',
    'runtime:log',
    'setup:progress',
    'setup:log',
    'vision:prefetch-progress',
    'permission:requested',
    'mcp:tool-discovered',
    'notification:clicked',
    'update:available',
    'update:not-available',
    'update:download-progress',
    'update:downloaded',
    'update:installing',
    'update:error',
    'config:changed',
];
// ---------------------------------------------------------------------------
// NLS Desktop API exposed to the renderer
// ---------------------------------------------------------------------------
const nlsDesktopApi = {
    platform: process.platform,
    isDesktop: true,
    /** Filled synchronously from %APPDATA%/babo-desktop/nls-config.json */
    boot: bootConfig,
    /** Re-read after setup saves nestjsUrl (boot snapshot can be stale until reload). */
    getBoot: () => readBootConfig(),
    getVersion: () => electron_1.ipcRenderer.invoke('app:version'),
    // ─── Config ───────────────────────────────────────────────────
    config: {
        get: () => electron_1.ipcRenderer.invoke('config:get'),
        set: (partial) => electron_1.ipcRenderer.invoke('config:set', partial),
        reset: () => electron_1.ipcRenderer.invoke('config:reset'),
        testConnection: (url) => electron_1.ipcRenderer.invoke('config:test-connection', url),
    },
    // ─── Setup (Python venv) ──────────────────────────────────────
    setup: {
        check: () => electron_1.ipcRenderer.invoke('setup:check'),
        start: () => electron_1.ipcRenderer.invoke('setup:start'),
        reset: () => electron_1.ipcRenderer.invoke('setup:reset'),
    },
    capabilities: {
        scanDevice: () => electron_1.ipcRenderer.invoke('capabilities:scan-device'),
        probeLan: (host, gpuWorkerSecret) => electron_1.ipcRenderer.invoke('capabilities:probe-lan', host, gpuWorkerSecret),
        recommend: (scan, gpuWorkerSecret) => electron_1.ipcRenderer.invoke('capabilities:recommend', scan, gpuWorkerSecret),
        testInference: (url, apiKey) => electron_1.ipcRenderer.invoke('capabilities:test-inference', url, apiKey),
        prefetchVision: () => electron_1.ipcRenderer.invoke('capabilities:prefetch-vision'),
        applyProfile: (profile) => electron_1.ipcRenderer.invoke('capabilities:apply-profile', profile),
    },
    // ─── Runtime (Agent runtime process) ──────────────────────────
    runtime: {
        getStatus: () => electron_1.ipcRenderer.invoke('runtime:status'),
        start: () => electron_1.ipcRenderer.invoke('runtime:start'),
        stop: () => electron_1.ipcRenderer.invoke('runtime:stop'),
        restart: () => electron_1.ipcRenderer.invoke('runtime:restart'),
        getLogs: (lines) => electron_1.ipcRenderer.invoke('runtime:logs', lines),
        hotReloadInference: (body) => electron_1.ipcRenderer.invoke('runtime:hot-reload-inference', body),
    },
    // ─── URLs (where Angular should connect) ──────────────────────
    getUrls: () => electron_1.ipcRenderer.invoke('urls:get'),
    backend: {
        ping: (nestjsUrl) => electron_1.ipcRenderer.invoke('backend:ping', nestjsUrl),
    },
    // ─── File System (permission-gated) ───────────────────────────
    readFile: (filePath) => electron_1.ipcRenderer.invoke('fs:readFile', filePath),
    writeFile: (filePath, content) => electron_1.ipcRenderer.invoke('fs:writeFile', filePath, content),
    readDir: (dirPath) => electron_1.ipcRenderer.invoke('fs:readDir', dirPath),
    stat: (filePath) => electron_1.ipcRenderer.invoke('fs:stat', filePath),
    showOpenDialog: (options) => electron_1.ipcRenderer.invoke('dialog:open', options),
    showSaveDialog: (options) => electron_1.ipcRenderer.invoke('dialog:save', options),
    // ─── Shell (permission-gated) ─────────────────────────────────
    execCommand: (command, cwd) => electron_1.ipcRenderer.invoke('shell:exec', command, cwd),
    // ─── Clipboard ────────────────────────────────────────────────
    readClipboard: () => electron_1.ipcRenderer.invoke('clipboard:read'),
    writeClipboard: (text) => electron_1.ipcRenderer.invoke('clipboard:write', text),
    // ─── System ───────────────────────────────────────────────────
    getSystemInfo: () => electron_1.ipcRenderer.invoke('system:info'),
    showNotification: (title, body) => electron_1.ipcRenderer.invoke('notification:show', title, body),
    // ─── Permissions ──────────────────────────────────────────────
    permissions: {
        getAll: () => electron_1.ipcRenderer.invoke('permissions:get'),
        getProfiles: () => electron_1.ipcRenderer.invoke('permissions:get-profiles'),
        applyProfile: (profileName) => electron_1.ipcRenderer.invoke('permissions:apply-profile', profileName),
        reset: () => electron_1.ipcRenderer.invoke('permissions:reset'),
        request: (permission, reason) => electron_1.ipcRenderer.invoke('permissions:request', permission, reason),
    },
    /** @deprecated use permissions.getAll */
    getPermissions: () => electron_1.ipcRenderer.invoke('permissions:get'),
    /** @deprecated use permissions.request */
    requestPermission: (permission, reason) => electron_1.ipcRenderer.invoke('permissions:request', permission, reason),
    // ─── Updates ──────────────────────────────────────────────────
    update: {
        check: () => electron_1.ipcRenderer.invoke('update:check'),
        download: () => electron_1.ipcRenderer.invoke('update:download'),
        install: () => electron_1.ipcRenderer.invoke('update:install'),
        snooze: (durationMs) => electron_1.ipcRenderer.invoke('update:snooze', durationMs),
        getStatus: () => electron_1.ipcRenderer.invoke('update:status'),
    },
    // ─── Auth Window ──────────────────────────────────────────────
    openAuthWindow: (url) => electron_1.ipcRenderer.invoke('browser:open-auth-window', url),
    openExternal: (url) => electron_1.ipcRenderer.invoke('shell:open-external', url),
    // ─── Browser: inject cookies into the webview partition ────────
    setBrowserCookies: (cookies) => electron_1.ipcRenderer.invoke('browser:set-cookies', cookies),
    // ─── MCP (stubs) ──────────────────────────────────────────────
    listMcpServers: () => electron_1.ipcRenderer.invoke('mcp:list'),
    addMcpServer: (config) => electron_1.ipcRenderer.invoke('mcp:add', config),
    removeMcpServer: (name) => electron_1.ipcRenderer.invoke('mcp:remove', name),
    // ─── Events (main -> renderer) ────────────────────────────────
    on: (channel, callback) => {
        if (VALID_CHANNELS.includes(channel)) {
            electron_1.ipcRenderer.on(channel, (_event, ...args) => callback(...args));
        }
    },
    removeListener: (channel, callback) => {
        electron_1.ipcRenderer.removeListener(channel, callback);
    },
};
// ---------------------------------------------------------------------------
// Expose to renderer as window.nls
// ---------------------------------------------------------------------------
electron_1.contextBridge.exposeInMainWorld('nls', nlsDesktopApi);
//# sourceMappingURL=preload.js.map