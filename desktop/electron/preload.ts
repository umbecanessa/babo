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

import { contextBridge, ipcRenderer } from 'electron';

export interface BaboBootConfig {
  nestjsUrl: string;
  apiUrl: string;
  runtimeUrl: string;
  runtimePort: number;
}

function readBootConfig(): BaboBootConfig | null {
  try {
    return ipcRenderer.sendSync('config:boot') as BaboBootConfig;
  } catch {
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
  getBoot: (): BaboBootConfig | null => readBootConfig(),

  getVersion: (): Promise<string> => ipcRenderer.invoke('app:version'),

  // ─── Config ───────────────────────────────────────────────────

  config: {
    get: (): Promise<Record<string, any>> =>
      ipcRenderer.invoke('config:get'),

    set: (partial: Record<string, any>): Promise<Record<string, any>> =>
      ipcRenderer.invoke('config:set', partial),

    reset: (): Promise<Record<string, any>> =>
      ipcRenderer.invoke('config:reset'),

    testConnection: (url: string): Promise<{ ok: boolean; message: string; latency: number }> =>
      ipcRenderer.invoke('config:test-connection', url),
  },

  // ─── Setup (Python venv) ──────────────────────────────────────

  setup: {
    check: (): Promise<{ setupComplete: boolean; venvReady: boolean; status: any }> =>
      ipcRenderer.invoke('setup:check'),

    start: (): Promise<any> =>
      ipcRenderer.invoke('setup:start'),

    reset: (): Promise<any> =>
      ipcRenderer.invoke('setup:reset'),
  },

  capabilities: {
    scanDevice: (): Promise<any> =>
      ipcRenderer.invoke('capabilities:scan-device'),

    probeLan: (host: string, gpuWorkerSecret?: string): Promise<any> =>
      ipcRenderer.invoke('capabilities:probe-lan', host, gpuWorkerSecret),

    recommend: (scan: any, gpuWorkerSecret?: string): Promise<any> =>
      ipcRenderer.invoke('capabilities:recommend', scan, gpuWorkerSecret),

    testInference: (
      url: string,
      apiKey?: string,
    ): Promise<{ ok: boolean; message: string; latency: number; models: string[] }> =>
      ipcRenderer.invoke('capabilities:test-inference', url, apiKey),

    prefetchVision: (): Promise<any> =>
      ipcRenderer.invoke('capabilities:prefetch-vision'),

    applyProfile: (profile: any): Promise<any> =>
      ipcRenderer.invoke('capabilities:apply-profile', profile),
  },

  // ─── Runtime (Agent runtime process) ──────────────────────────

  runtime: {
    getStatus: (): Promise<{
      running: boolean;
      port: number;
      pid: number | null;
      uptime: number;
      agents: number;
      error: string | null;
      agentEnergy: Record<string, { energy_level: number; agent_status: string }>;
    }> => ipcRenderer.invoke('runtime:status'),

    start: (): Promise<any> =>
      ipcRenderer.invoke('runtime:start'),

    stop: (): Promise<any> =>
      ipcRenderer.invoke('runtime:stop'),

    restart: (): Promise<any> =>
      ipcRenderer.invoke('runtime:restart'),

    getLogs: (lines?: number): Promise<string[]> =>
      ipcRenderer.invoke('runtime:logs', lines),

    hotReloadInference: (body: Record<string, unknown>): Promise<unknown> =>
      ipcRenderer.invoke('runtime:hot-reload-inference', body),
  },

  // ─── URLs (where Angular should connect) ──────────────────────

  getUrls: (): Promise<{
    runtimeUrl: string;
    nestjsUrl: string;
    apiUrl: string;
    wsUrl: string;
  }> => ipcRenderer.invoke('urls:get'),

  backend: {
    ping: (
      nestjsUrl?: string,
    ): Promise<{
      ok: boolean;
      statusCode: number;
      latency: number;
      message: string;
      apiBase: string;
      nestjsUrl: string;
    }> => ipcRenderer.invoke('backend:ping', nestjsUrl),
  },

  // ─── File System (permission-gated) ───────────────────────────

  readFile: (filePath: string): Promise<string> =>
    ipcRenderer.invoke('fs:readFile', filePath),

  writeFile: (filePath: string, content: string): Promise<void> =>
    ipcRenderer.invoke('fs:writeFile', filePath, content),

  readDir: (
    dirPath: string,
  ): Promise<Array<{ name: string; isDirectory: boolean; size: number }>> =>
    ipcRenderer.invoke('fs:readDir', dirPath),

  stat: (filePath: string): Promise<{ isFile: boolean; isDirectory: boolean }> =>
    ipcRenderer.invoke('fs:stat', filePath),

  showOpenDialog: (
    options: Record<string, unknown>,
  ): Promise<{ canceled: boolean; filePaths: string[] }> =>
    ipcRenderer.invoke('dialog:open', options),

  showSaveDialog: (
    options: Record<string, unknown>,
  ): Promise<{ canceled: boolean; filePath: string | undefined }> =>
    ipcRenderer.invoke('dialog:save', options),

  // ─── Shell (permission-gated) ─────────────────────────────────

  execCommand: (
    command: string,
    cwd?: string,
  ): Promise<{ stdout: string; stderr: string; exitCode: number }> =>
    ipcRenderer.invoke('shell:exec', command, cwd),

  // ─── Clipboard ────────────────────────────────────────────────

  readClipboard: (): Promise<string> => ipcRenderer.invoke('clipboard:read'),
  writeClipboard: (text: string): Promise<void> =>
    ipcRenderer.invoke('clipboard:write', text),

  getLaunchAttributionRef: (): Promise<string | null> =>
    ipcRenderer.invoke('analytics:launch-ref'),

  // ─── System ───────────────────────────────────────────────────

  getSystemInfo: (): Promise<{
    platform: string;
    arch: string;
    cpus: number;
    totalMemory: number;
    freeMemory: number;
    hostname: string;
  }> => ipcRenderer.invoke('system:info'),

  showNotification: (title: string, body: string): Promise<void> =>
    ipcRenderer.invoke('notification:show', title, body),

  // ─── Permissions ──────────────────────────────────────────────

  permissions: {
    getAll: (): Promise<Record<string, boolean>> =>
      ipcRenderer.invoke('permissions:get'),

    getProfiles: (): Promise<
      Array<{ name: string; description: string; grants: Record<string, boolean> }>
    > => ipcRenderer.invoke('permissions:get-profiles'),

    applyProfile: (profileName: string): Promise<Record<string, boolean>> =>
      ipcRenderer.invoke('permissions:apply-profile', profileName),

    reset: (): Promise<Record<string, boolean>> =>
      ipcRenderer.invoke('permissions:reset'),

    request: (permission: string, reason: string): Promise<boolean> =>
      ipcRenderer.invoke('permissions:request', permission, reason),
  },

  /** @deprecated use permissions.getAll */
  getPermissions: (): Promise<Record<string, boolean>> =>
    ipcRenderer.invoke('permissions:get'),

  /** @deprecated use permissions.request */
  requestPermission: (permission: string, reason: string): Promise<boolean> =>
    ipcRenderer.invoke('permissions:request', permission, reason),

  // ─── Updates ──────────────────────────────────────────────────

  update: {
    check: (): Promise<any> =>
      ipcRenderer.invoke('update:check'),

    download: (): Promise<void> =>
      ipcRenderer.invoke('update:download'),

    install: (): Promise<void> =>
      ipcRenderer.invoke('update:install'),

    snooze: (durationMs: number): Promise<any> =>
      ipcRenderer.invoke('update:snooze', durationMs),

    getStatus: (): Promise<{
      state: string;
      version: string | null;
      releaseNotes: string | null;
      releaseDate: string | null;
      progress: any;
      error: string | null;
      snoozeUntil: number | null;
    }> => ipcRenderer.invoke('update:status'),

  },

  // ─── Auth Window ──────────────────────────────────────────────

  openAuthWindow: (url: string): Promise<{ success: boolean }> =>
    ipcRenderer.invoke('browser:open-auth-window', url),

  openExternal: (url: string): Promise<void> =>
    ipcRenderer.invoke('shell:open-external', url),

  // ─── Browser: inject cookies into the webview partition ────────

  setBrowserCookies: (cookies: Array<Record<string, unknown>>): Promise<{ ok: number; fail: number }> =>
    ipcRenderer.invoke('browser:set-cookies', cookies),

  // ─── MCP (stubs) ──────────────────────────────────────────────

  listMcpServers: (): Promise<Array<{ name: string; connected: boolean; tools: number }>> =>
    ipcRenderer.invoke('mcp:list'),

  addMcpServer: (config: Record<string, unknown>): Promise<void> =>
    ipcRenderer.invoke('mcp:add', config),

  removeMcpServer: (name: string): Promise<void> =>
    ipcRenderer.invoke('mcp:remove', name),

  // ─── Events (main -> renderer) ────────────────────────────────

  on: (channel: string, callback: (...args: unknown[]) => void): void => {
    if (VALID_CHANNELS.includes(channel)) {
      ipcRenderer.on(channel, (_event, ...args) => callback(...args));
    }
  },

  removeListener: (channel: string, callback: (...args: unknown[]) => void): void => {
    ipcRenderer.removeListener(channel, callback);
  },
};

// ---------------------------------------------------------------------------
// Expose to renderer as window.nls
// ---------------------------------------------------------------------------

contextBridge.exposeInMainWorld('nls', nlsDesktopApi);

export type NlsDesktopApi = typeof nlsDesktopApi;
