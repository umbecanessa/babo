"use strict";
/**
 * NLS Desktop App -- IPC Handlers
 *
 * Registers all IPC handlers that the preload script exposes to the renderer.
 * Organized by namespace: config, setup, runtime, filesystem, shell, etc.
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
exports.registerIpcHandlers = registerIpcHandlers;
const electron_1 = require("electron");
const fs = __importStar(require("fs"));
const path = __importStar(require("path"));
const os = __importStar(require("os"));
const http = __importStar(require("http"));
const child_process_1 = require("child_process");
// ---------------------------------------------------------------------------
// Register all IPC handlers
// ---------------------------------------------------------------------------
function registerIpcHandlers(permissions, runtime, venv, config, updater) {
    // ─── App ──────────────────────────────────────────────────────
    electron_1.ipcMain.handle('app:version', () => electron_1.app.getVersion());
    // ─── Config ───────────────────────────────────────────────────
    electron_1.ipcMain.handle('config:get', () => config.get());
    electron_1.ipcMain.handle('config:set', (_event, partial) => {
        return config.set(partial);
    });
    electron_1.ipcMain.handle('config:reset', () => config.reset());
    electron_1.ipcMain.handle('config:test-connection', async (_event, url) => {
        return testConnection(url);
    });
    // ─── Setup (Python venv) ──────────────────────────────────────
    electron_1.ipcMain.handle('setup:check', () => ({
        setupComplete: config.isSetupComplete(),
        venvReady: venv.isReady(),
        status: venv.status,
    }));
    electron_1.ipcMain.handle('setup:start', async () => {
        await venv.setup();
        config.set({ setupComplete: true });
        return venv.status;
    });
    electron_1.ipcMain.handle('setup:reset', async () => {
        await venv.reset();
        config.set({ setupComplete: false });
        return venv.status;
    });
    // ─── Runtime (Agent runtime process) ──────────────────────────
    electron_1.ipcMain.handle('runtime:status', () => runtime.getStatus());
    electron_1.ipcMain.handle('runtime:start', async () => {
        await runtime.start();
        return runtime.getStatus();
    });
    electron_1.ipcMain.handle('runtime:stop', async () => {
        await runtime.stop();
        return runtime.getStatus();
    });
    electron_1.ipcMain.handle('runtime:restart', async () => {
        await runtime.restart();
        return runtime.getStatus();
    });
    electron_1.ipcMain.handle('runtime:logs', (_event, lines) => {
        return runtime.getLogs(lines);
    });
    // ─── URLs (for Angular to know where to connect) ──────────────
    electron_1.ipcMain.handle('urls:get', () => {
        const cfg = config.get();
        return {
            runtimeUrl: `http://127.0.0.1:${cfg.runtimePort}`,
            nestjsUrl: cfg.nestjsUrl,
            wsUrl: `ws://127.0.0.1:${cfg.runtimePort}`,
        };
    });
    // ─── File System (permission-gated) ───────────────────────────
    electron_1.ipcMain.handle('fs:readFile', async (_event, filePath) => {
        await permissions.require('filesystem.read', filePath);
        return fs.promises.readFile(filePath, 'utf-8');
    });
    electron_1.ipcMain.handle('fs:writeFile', async (_event, filePath, content) => {
        await permissions.require('filesystem.write', filePath);
        await fs.promises.mkdir(path.dirname(filePath), { recursive: true });
        await fs.promises.writeFile(filePath, content, 'utf-8');
    });
    electron_1.ipcMain.handle('fs:readDir', async (_event, dirPath) => {
        await permissions.require('filesystem.read', dirPath);
        const entries = await fs.promises.readdir(dirPath, { withFileTypes: true });
        return Promise.all(entries.map(async (entry) => {
            let size = 0;
            try {
                if (!entry.isDirectory()) {
                    const stat = await fs.promises.stat(path.join(dirPath, entry.name));
                    size = stat.size;
                }
            }
            catch {
                // ignore stat errors
            }
            return {
                name: entry.name,
                isDirectory: entry.isDirectory(),
                size,
            };
        }));
    });
    // ─── Dialogs ──────────────────────────────────────────────────
    electron_1.ipcMain.handle('dialog:open', async (_event, options) => {
        const win = electron_1.BrowserWindow.getFocusedWindow();
        if (!win)
            return { canceled: true, filePaths: [] };
        return electron_1.dialog.showOpenDialog(win, options);
    });
    electron_1.ipcMain.handle('dialog:save', async (_event, options) => {
        const win = electron_1.BrowserWindow.getFocusedWindow();
        if (!win)
            return { canceled: true, filePath: undefined };
        return electron_1.dialog.showSaveDialog(win, options);
    });
    // ─── Shell (permission-gated) ─────────────────────────────────
    electron_1.ipcMain.handle('shell:exec', async (_event, command, cwd) => {
        await permissions.require('shell.execute', command);
        return new Promise((resolve) => {
            (0, child_process_1.exec)(command, {
                cwd: cwd || os.homedir(),
                timeout: 30_000,
                maxBuffer: 10 * 1024 * 1024,
            }, (error, stdout, stderr) => {
                resolve({
                    stdout: stdout || '',
                    stderr: stderr || '',
                    exitCode: error?.code ?? 0,
                });
            });
        });
    });
    // ─── Clipboard ────────────────────────────────────────────────
    electron_1.ipcMain.handle('clipboard:read', async () => {
        await permissions.require('clipboard.read');
        return electron_1.clipboard.readText();
    });
    electron_1.ipcMain.handle('clipboard:write', async (_event, text) => {
        await permissions.require('clipboard.write');
        electron_1.clipboard.writeText(text);
    });
    // ─── System Info ──────────────────────────────────────────────
    electron_1.ipcMain.handle('system:info', () => ({
        platform: os.platform(),
        arch: os.arch(),
        cpus: os.cpus().length,
        totalMemory: os.totalmem(),
        freeMemory: os.freemem(),
        hostname: os.hostname(),
    }));
    // ─── Notifications ────────────────────────────────────────────
    electron_1.ipcMain.handle('notification:show', async (_event, title, body) => {
        await permissions.require('notification');
        new electron_1.Notification({ title, body }).show();
    });
    // ─── Permissions ──────────────────────────────────────────────
    electron_1.ipcMain.handle('permissions:get', () => permissions.getAll());
    electron_1.ipcMain.handle('permissions:request', async (_event, permission, reason) => {
        return permissions.request(permission, reason);
    });
    // ─── Updates ──────────────────────────────────────────────────
    electron_1.ipcMain.handle('update:check', () => updater.checkForUpdates());
    electron_1.ipcMain.handle('update:download', () => updater.downloadUpdate());
    electron_1.ipcMain.handle('update:install', () => updater.installUpdate());
    electron_1.ipcMain.handle('update:snooze', (_event, durationMs) => {
        return updater.snooze(durationMs);
    });
    electron_1.ipcMain.handle('update:status', () => updater.getStatus());
    // ─── Auth Window ────────────────────────────────────────────────
    //
    // Opens the user's real system browser (Chrome / Safari / Firefox)
    // for sign-in.  Providers like Google block embedded Electron
    // windows, but they cannot block the user's default browser.
    // A native dialog waits for the user to confirm they have finished.
    electron_1.ipcMain.handle('browser:open-auth-window', async (_event, url) => {
        await electron_1.shell.openExternal(url);
        const parent = electron_1.BrowserWindow.getFocusedWindow() ?? undefined;
        const { response } = await electron_1.dialog.showMessageBox(parent, {
            type: 'info',
            title: 'Sign in',
            message: 'A sign-in page has been opened in your browser.',
            detail: 'Please complete the sign-in in your browser, then click "Done" to continue.\n\n' +
                'If you cancelled or could not sign in, click "Cancel".',
            buttons: ['Done', 'Cancel'],
            defaultId: 0,
            cancelId: 1,
        });
        return { success: response === 0 };
    });
    electron_1.ipcMain.handle('shell:open-external', async (_event, url) => {
        await electron_1.shell.openExternal(url);
    });
    // ─── Browser: inject cookies into the webview partition ────────
    //
    // The webview uses partition="persist:nls-agent".  CDP cannot
    // reliably set cookies on Electron partitioned sessions, so we
    // use Electron's native session.cookies API instead.
    electron_1.ipcMain.handle('browser:set-cookies', async (_event, cookies) => {
        const ses = electron_1.session.fromPartition('persist:nls-agent');
        let ok = 0;
        let fail = 0;
        for (const c of cookies) {
            try {
                const domain = c.domain || '';
                // Force SameSite=None so cookies survive Electron's
                // third-party classification (all cookies are third-party
                // in an Electron webview).
                await ses.cookies.set({
                    url: `https://${domain.replace(/^\./, '')}${c.path || '/'}`,
                    name: c.name || '',
                    value: c.value || '',
                    domain,
                    path: c.path || '/',
                    secure: true,
                    httpOnly: !!(c.httpOnly ?? c.http_only),
                    sameSite: 'no_restriction',
                    expirationDate: typeof c.expires === 'number' && c.expires > 0
                        ? c.expires
                        : undefined,
                });
                ok++;
            }
            catch {
                fail++;
            }
        }
        return { ok, fail };
    });
    // ─── MCP (stubs -- Phase 5) ───────────────────────────────────
    electron_1.ipcMain.handle('mcp:list', () => []);
    electron_1.ipcMain.handle('mcp:add', async () => { });
    electron_1.ipcMain.handle('mcp:remove', async () => { });
}
// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
async function testConnection(url) {
    const MAX_RETRIES = 3;
    const RETRY_DELAY = 1_000;
    for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
        const result = await testConnectionOnce(url);
        if (result.ok)
            return result;
        // Retry on DNS failures (cold cache); give up immediately on other errors
        const isDnsError = result.message.includes('ENOTFOUND') || result.message.includes('EAI_AGAIN');
        if (!isDnsError || attempt === MAX_RETRIES)
            return result;
        await new Promise((r) => setTimeout(r, RETRY_DELAY));
    }
    return { ok: false, message: 'Connection failed after retries', latency: 0 };
}
function testConnectionOnce(url) {
    return new Promise((resolve) => {
        const start = Date.now();
        const target = url.endsWith('/health') ? url : `${url}/health`;
        const req = http.get(target, { timeout: 5_000 }, (res) => {
            const latency = Date.now() - start;
            if (res.statusCode === 200) {
                resolve({ ok: true, message: 'Connected', latency });
            }
            else {
                resolve({ ok: false, message: `HTTP ${res.statusCode}`, latency });
            }
        });
        req.on('error', (err) => {
            resolve({
                ok: false,
                message: `Connection failed: ${err.message}`,
                latency: Date.now() - start,
            });
        });
        req.on('timeout', () => {
            req.destroy();
            resolve({
                ok: false,
                message: 'Connection timed out (5s)',
                latency: 5000,
            });
        });
    });
}
//# sourceMappingURL=ipc-handlers.js.map