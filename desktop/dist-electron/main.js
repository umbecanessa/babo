"use strict";
/**
 * NLS Desktop App -- Electron Main Process
 *
 * Architecture:
 *   Angular (renderer)
 *     ├── Auth/Admin → NestJS on Railway (cloud)
 *     └── Agent ops  → Local Python Runtime (localhost:9222)
 *
 *   Python Agent Runtime (sidecar)
 *     ├── Inference → OpenAI-compatible API
 *     └── Sleep     → consolidation on the Python runtime
 *
 * On first launch, the app runs a setup wizard (Python venv creation,
 * runtime connection configuration).  On subsequent launches, the runtime
 * starts automatically.
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
const electron_1 = require("electron");
const path = __importStar(require("path"));
const ipc_handlers_1 = require("./ipc-handlers");
const runtime_manager_1 = require("./runtime-manager");
const venv_manager_1 = require("./venv-manager");
const config_manager_1 = require("./config-manager");
/** file:// renderer cannot always read cross-origin responses; widen CORS for the cloud API. */
function enableRendererCorsForBackend(nestjsUrl) {
    let origin = '';
    try {
        origin = new URL(nestjsUrl).origin;
    }
    catch {
        return;
    }
    electron_1.session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
        if (!details.url.startsWith(origin)) {
            callback({ responseHeaders: details.responseHeaders });
            return;
        }
        const responseHeaders = { ...details.responseHeaders };
        responseHeaders['Access-Control-Allow-Origin'] = ['*'];
        responseHeaders['Access-Control-Allow-Methods'] = [
            'GET, POST, PUT, PATCH, DELETE, OPTIONS, HEAD',
        ];
        responseHeaders['Access-Control-Allow-Headers'] = ['*'];
        callback({ responseHeaders });
    });
}
const permission_manager_1 = require("./permission-manager");
const update_manager_1 = require("./update-manager");
// ---------------------------------------------------------------------------
// Globals
// ---------------------------------------------------------------------------
let mainWindow = null;
let runtimeManager = null;
let venvManager = null;
let configManager = null;
let permissionManager = null;
let updateManager = null;
const isDev = process.env['NODE_ENV'] === 'development';
const ANGULAR_DEV_URL = 'http://localhost:4200';
const CDP_PORT = 9245;
// ---------------------------------------------------------------------------
// Window creation
// ---------------------------------------------------------------------------
function createMainWindow() {
    mainWindow = new electron_1.BrowserWindow({
        width: 1440,
        height: 900,
        minWidth: 800,
        minHeight: 600,
        title: 'Babo',
        backgroundColor: '#0a0a0a',
        titleBarStyle: 'hiddenInset',
        trafficLightPosition: { x: 16, y: 16 },
        webPreferences: {
            preload: path.join(__dirname, 'preload.js'),
            contextIsolation: true,
            nodeIntegration: false,
            sandbox: false,
            webSecurity: true,
            webviewTag: true,
        },
        show: false,
    });
    mainWindow.once('ready-to-show', () => {
        mainWindow?.show();
    });
    if (isDev) {
        mainWindow.loadURL(ANGULAR_DEV_URL);
        mainWindow.webContents.openDevTools({ mode: 'detach' });
    }
    else {
        const rendererPath = path.join(process.resourcesPath, 'renderer');
        mainWindow.loadFile(path.join(rendererPath, 'index.html'));
    }
    // Ctrl+Shift+I to open DevTools in any mode (debug aid)
    mainWindow.webContents.on('before-input-event', (_event, input) => {
        if (input.control && input.shift && input.key.toLowerCase() === 'i') {
            mainWindow?.webContents.toggleDevTools();
        }
    });
    mainWindow.webContents.setWindowOpenHandler(({ url }) => {
        electron_1.shell.openExternal(url);
        return { action: 'deny' };
    });
    mainWindow.on('closed', () => {
        mainWindow = null;
    });
}
// ---------------------------------------------------------------------------
// Application menu
// ---------------------------------------------------------------------------
function buildAppMenu() {
    const isMac = process.platform === 'darwin';
    if (!isMac) {
        electron_1.Menu.setApplicationMenu(null);
        return;
    }
    const template = [
        {
            label: electron_1.app.name,
            submenu: [
                { role: 'about' },
                { type: 'separator' },
                { role: 'hide' },
                { role: 'hideOthers' },
                { role: 'unhide' },
                { type: 'separator' },
                { role: 'quit' },
            ],
        },
        {
            label: 'Edit',
            submenu: [
                { role: 'undo' },
                { role: 'redo' },
                { type: 'separator' },
                { role: 'cut' },
                { role: 'copy' },
                { role: 'paste' },
                { role: 'selectAll' },
            ],
        },
        {
            label: 'Window',
            submenu: [
                { role: 'minimize' },
                { role: 'zoom' },
                { role: 'togglefullscreen' },
                ...(isDev
                    ? [
                        { type: 'separator' },
                        { role: 'reload' },
                        { role: 'forceReload' },
                        { role: 'toggleDevTools' },
                    ]
                    : []),
            ],
        },
    ];
    electron_1.Menu.setApplicationMenu(electron_1.Menu.buildFromTemplate(template));
}
// ---------------------------------------------------------------------------
// Auto-start runtime
// ---------------------------------------------------------------------------
async function autoStartRuntime() {
    if (!configManager || !runtimeManager || !venvManager)
        return;
    if (!configManager.isSetupComplete())
        return;
    if (!venvManager.isReady())
        return;
    try {
        // Sync pip dependencies if requirements-desktop.txt changed (e.g. after an app update)
        await venvManager.checkDepsSync();
    }
    catch (err) {
        console.error('Dependency sync failed:', err.message);
    }
    try {
        await runtimeManager.start();
    }
    catch (err) {
        console.error('Failed to auto-start runtime:', err.message);
    }
}
// ---------------------------------------------------------------------------
// App lifecycle
// ---------------------------------------------------------------------------
// Enable CDP so Playwright can connect to the in-app webview.
electron_1.app.commandLine.appendSwitch('remote-debugging-port', String(CDP_PORT));
// Allow third-party cookies in the agent webview partition.
// Google OAuth and many other auth flows require cross-domain cookies.
// Chromium 130+ (Electron 33+) phases these out by default.
electron_1.app.commandLine.appendSwitch('disable-features', 'ThirdPartyCookiePhaseout,TrackingProtection3pcd');
electron_1.app.whenReady().then(async () => {
    // Pre-initialize the agent webview partition so cookies, storage,
    // and permissions work correctly before the first navigation.
    //
    // Electron treats ALL cookies as third-party (the app origin differs
    // from every website).  Google and other providers check for working
    // cookies and refuse to load if they fail.  We fix this by:
    //   1. Granting all permission requests in the partition.
    //   2. Intercepting every HTTP response to rewrite Set-Cookie headers
    //      so Chromium's SameSite enforcement doesn't reject them.
    const agentSession = electron_1.session.fromPartition('persist:nls-agent');
    agentSession.setPermissionRequestHandler((_wc, _permission, callback) => {
        callback(true);
    });
    agentSession.setPermissionCheckHandler(() => true);
    // Rewrite Set-Cookie headers: add SameSite=None; Secure so cookies
    // survive Chromium's third-party classification in Electron.
    agentSession.webRequest.onHeadersReceived((details, callback) => {
        const headers = details.responseHeaders || {};
        const key = Object.keys(headers).find((k) => k.toLowerCase() === 'set-cookie');
        if (key) {
            headers[key] = headers[key].map((cookie) => {
                let c = cookie;
                // Strip any existing SameSite directive
                c = c.replace(/;\s*SameSite\s*=\s*\w+/gi, '');
                // Append SameSite=None; Secure so the cookie is accepted
                c += '; SameSite=None; Secure';
                return c;
            });
        }
        callback({ responseHeaders: headers });
    });
    // Initialize managers
    configManager = new config_manager_1.ConfigManager();
    enableRendererCorsForBackend(configManager.get().nestjsUrl);
    venvManager = new venv_manager_1.VenvManager();
    permissionManager = new permission_manager_1.PermissionManager();
    runtimeManager = new runtime_manager_1.RuntimeManager(configManager, venvManager);
    updateManager = new update_manager_1.UpdateManager(runtimeManager, configManager);
    // Register IPC handlers
    (0, ipc_handlers_1.registerIpcHandlers)(permissionManager, runtimeManager, venvManager, configManager, updateManager);
    buildAppMenu();
    createMainWindow();
    // Start auto-updater (periodic checks + unattended install logic)
    if (mainWindow && updateManager) {
        updateManager.initialize(mainWindow);
    }
    // Auto-start runtime if setup is complete
    autoStartRuntime();
    electron_1.app.on('activate', () => {
        if (electron_1.BrowserWindow.getAllWindows().length === 0) {
            createMainWindow();
        }
    });
});
electron_1.app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') {
        electron_1.app.quit();
    }
});
let isQuitting = false;
electron_1.app.on('before-quit', (event) => {
    if (isQuitting || !runtimeManager)
        return;
    // When autoUpdater.quitAndInstall() triggers the quit, let it proceed
    // without preventDefault so the update-and-relaunch cycle completes
    // (critical on macOS where the .app bundle is swapped in-place).
    if (updateManager?.isUpdating) {
        isQuitting = true;
        updateManager.dispose();
        runtimeManager.stop().catch(() => { });
        return;
    }
    // Normal quit: prevent, do async cleanup, then force exit.
    event.preventDefault();
    isQuitting = true;
    console.warn('[SHUTDOWN_TRACE] app.before-quit calling runtime.stop()');
    updateManager?.dispose();
    runtimeManager.stop().finally(() => {
        electron_1.app.exit(0);
    });
});
//# sourceMappingURL=main.js.map