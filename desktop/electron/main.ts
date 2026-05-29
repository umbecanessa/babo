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

import { app, BrowserWindow, Menu, shell, session } from 'electron';
import * as path from 'path';
import { registerIpcHandlers } from './ipc-handlers';
import { RuntimeManager } from './runtime-manager';
import { VenvManager } from './venv-manager';
import { ConfigManager } from './config-manager';

/** file:// renderer cannot always read cross-origin responses; widen CORS for the cloud API. */
function enableRendererCorsForBackend(nestjsUrl: string): void {
  let origin = '';
  try {
    origin = new URL(nestjsUrl).origin;
  } catch {
    return;
  }

  session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
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
import { PermissionManager } from './permission-manager';
import { UpdateManager } from './update-manager';

// ---------------------------------------------------------------------------
// Globals
// ---------------------------------------------------------------------------

let mainWindow: BrowserWindow | null = null;
let runtimeManager: RuntimeManager | null = null;
let venvManager: VenvManager | null = null;
let configManager: ConfigManager | null = null;
let permissionManager: PermissionManager | null = null;
let updateManager: UpdateManager | null = null;

const isDev = process.env['NODE_ENV'] === 'development';
const ANGULAR_DEV_URL = 'http://localhost:4200';
const CDP_PORT = 9245;

// ---------------------------------------------------------------------------
// Window creation
// ---------------------------------------------------------------------------

function createMainWindow(): void {
  mainWindow = new BrowserWindow({
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
  } else {
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
    shell.openExternal(url);
    return { action: 'deny' };
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

// ---------------------------------------------------------------------------
// Application menu
// ---------------------------------------------------------------------------

function buildAppMenu(): void {
  const isMac = process.platform === 'darwin';

  if (!isMac) {
    Menu.setApplicationMenu(null);
    return;
  }

  const template: Electron.MenuItemConstructorOptions[] = [
    {
      label: app.name,
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
              { type: 'separator' as const },
              { role: 'reload' as const },
              { role: 'forceReload' as const },
              { role: 'toggleDevTools' as const },
            ]
          : []),
      ],
    },
  ];

  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

// ---------------------------------------------------------------------------
// Auto-start runtime
// ---------------------------------------------------------------------------

async function autoStartRuntime(): Promise<void> {
  if (!configManager || !runtimeManager || !venvManager) return;
  if (!configManager.isSetupComplete()) return;
  if (!venvManager.isReady()) return;

  try {
    // Sync pip dependencies if requirements-desktop.txt changed (e.g. after an app update)
    await venvManager.checkDepsSync();
  } catch (err: any) {
    console.error('Dependency sync failed:', err.message);
  }

  try {
    await runtimeManager.start();
  } catch (err: any) {
    console.error('Failed to auto-start runtime:', err.message);
  }
}

// ---------------------------------------------------------------------------
// App lifecycle
// ---------------------------------------------------------------------------

// Enable CDP so Playwright can connect to the in-app webview.
app.commandLine.appendSwitch('remote-debugging-port', String(CDP_PORT));

// Allow third-party cookies in the agent webview partition.
// Google OAuth and many other auth flows require cross-domain cookies.
// Chromium 130+ (Electron 33+) phases these out by default.
app.commandLine.appendSwitch(
  'disable-features',
  'ThirdPartyCookiePhaseout,TrackingProtection3pcd',
);

app.whenReady().then(async () => {
  // Pre-initialize the agent webview partition so cookies, storage,
  // and permissions work correctly before the first navigation.
  //
  // Electron treats ALL cookies as third-party (the app origin differs
  // from every website).  Google and other providers check for working
  // cookies and refuse to load if they fail.  We fix this by:
  //   1. Granting all permission requests in the partition.
  //   2. Intercepting every HTTP response to rewrite Set-Cookie headers
  //      so Chromium's SameSite enforcement doesn't reject them.
  const agentSession = session.fromPartition('persist:nls-agent');
  agentSession.setPermissionRequestHandler((_wc, _permission, callback) => {
    callback(true);
  });
  agentSession.setPermissionCheckHandler(() => true);

  // Rewrite Set-Cookie headers: add SameSite=None; Secure so cookies
  // survive Chromium's third-party classification in Electron.
  agentSession.webRequest.onHeadersReceived((details, callback) => {
    const headers = details.responseHeaders || {};
    const key = Object.keys(headers).find(
      (k) => k.toLowerCase() === 'set-cookie',
    );
    if (key) {
      headers[key] = (headers[key] as string[]).map((cookie) => {
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
  configManager = new ConfigManager();
  enableRendererCorsForBackend(configManager.get().nestjsUrl);
  venvManager = new VenvManager();
  permissionManager = new PermissionManager();
  runtimeManager = new RuntimeManager(configManager, venvManager);
  updateManager = new UpdateManager(runtimeManager, configManager);

  // Register IPC handlers
  registerIpcHandlers(permissionManager, runtimeManager, venvManager, configManager, updateManager);

  buildAppMenu();
  createMainWindow();

  // Start auto-updater (periodic checks + unattended install logic)
  if (mainWindow && updateManager) {
    updateManager.initialize(mainWindow);
  }

  // Auto-start runtime if setup is complete
  autoStartRuntime();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createMainWindow();
    }
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

let isQuitting = false;
app.on('before-quit', (event) => {
  if (isQuitting || !runtimeManager) return;

  // When autoUpdater.quitAndInstall() triggers the quit, let it proceed
  // without preventDefault so the update-and-relaunch cycle completes
  // (critical on macOS where the .app bundle is swapped in-place).
  if (updateManager?.isUpdating) {
    isQuitting = true;
    updateManager.dispose();
    runtimeManager.stop().catch(() => {});
    return;
  }

  // Normal quit: prevent, do async cleanup, then force exit.
  event.preventDefault();
  isQuitting = true;
  console.warn('[SHUTDOWN_TRACE] app.before-quit calling runtime.stop()');
  updateManager?.dispose();
  runtimeManager.stop().finally(() => {
    app.exit(0);
  });
});
