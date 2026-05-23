/**
 * NLS Desktop App -- IPC Handlers
 *
 * Registers all IPC handlers that the preload script exposes to the renderer.
 * Organized by namespace: config, setup, runtime, filesystem, shell, etc.
 */

import { ipcMain, app, clipboard, dialog, Notification, BrowserWindow, shell, session } from 'electron';
import * as fs from 'fs';
import * as path from 'path';
import * as os from 'os';
import * as http from 'http';
import { exec } from 'child_process';
import { PermissionManager } from './permission-manager';
import { RuntimeManager } from './runtime-manager';
import { VenvManager } from './venv-manager';
import { ConfigManager } from './config-manager';
import { UpdateManager } from './update-manager';

// ---------------------------------------------------------------------------
// Register all IPC handlers
// ---------------------------------------------------------------------------

export function registerIpcHandlers(
  permissions: PermissionManager,
  runtime: RuntimeManager,
  venv: VenvManager,
  config: ConfigManager,
  updater: UpdateManager,
): void {
  // ─── App ──────────────────────────────────────────────────────

  ipcMain.handle('app:version', () => app.getVersion());

  // ─── Config ───────────────────────────────────────────────────

  ipcMain.handle('config:get', () => config.get());

  ipcMain.handle('config:set', (_event, partial: Record<string, unknown>) => {
    return config.set(partial);
  });

  ipcMain.handle('config:reset', () => config.reset());

  ipcMain.handle('config:test-connection', async (_event, url: string) => {
    return testConnection(url);
  });

  // ─── Setup (Python venv) ──────────────────────────────────────

  ipcMain.handle('setup:check', () => ({
    setupComplete: config.isSetupComplete(),
    venvReady: venv.isReady(),
    status: venv.status,
  }));

  ipcMain.handle('setup:start', async () => {
    await venv.setup();
    config.set({ setupComplete: true });
    return venv.status;
  });

  ipcMain.handle('setup:reset', async () => {
    await venv.reset();
    config.set({ setupComplete: false });
    return venv.status;
  });

  // ─── Runtime (Agent runtime process) ──────────────────────────

  ipcMain.handle('runtime:status', () => runtime.getStatus());

  ipcMain.handle('runtime:start', async () => {
    await runtime.start();
    return runtime.getStatus();
  });

  ipcMain.handle('runtime:stop', async () => {
    await runtime.stop();
    return runtime.getStatus();
  });

  ipcMain.handle('runtime:restart', async () => {
    await runtime.restart();
    return runtime.getStatus();
  });

  ipcMain.handle('runtime:logs', (_event, lines?: number) => {
    return runtime.getLogs(lines);
  });

  // ─── URLs (for Angular to know where to connect) ──────────────

  ipcMain.handle('urls:get', () => {
    const cfg = config.get();
    return {
      runtimeUrl: `http://127.0.0.1:${cfg.runtimePort}`,
      nestjsUrl: cfg.nestjsUrl,
      wsUrl: `ws://127.0.0.1:${cfg.runtimePort}`,
    };
  });

  // ─── File System (permission-gated) ───────────────────────────

  ipcMain.handle('fs:readFile', async (_event, filePath: string) => {
    await permissions.require('filesystem.read', filePath);
    return fs.promises.readFile(filePath, 'utf-8');
  });

  ipcMain.handle(
    'fs:writeFile',
    async (_event, filePath: string, content: string) => {
      await permissions.require('filesystem.write', filePath);
      await fs.promises.mkdir(path.dirname(filePath), { recursive: true });
      await fs.promises.writeFile(filePath, content, 'utf-8');
    },
  );

  ipcMain.handle('fs:readDir', async (_event, dirPath: string) => {
    await permissions.require('filesystem.read', dirPath);
    const entries = await fs.promises.readdir(dirPath, { withFileTypes: true });
    return Promise.all(
      entries.map(async (entry) => {
        let size = 0;
        try {
          if (!entry.isDirectory()) {
            const stat = await fs.promises.stat(path.join(dirPath, entry.name));
            size = stat.size;
          }
        } catch {
          // ignore stat errors
        }
        return {
          name: entry.name,
          isDirectory: entry.isDirectory(),
          size,
        };
      }),
    );
  });

  // ─── Dialogs ──────────────────────────────────────────────────

  ipcMain.handle(
    'dialog:open',
    async (_event, options: Electron.OpenDialogOptions) => {
      const win = BrowserWindow.getFocusedWindow();
      if (!win) return { canceled: true, filePaths: [] };
      return dialog.showOpenDialog(win, options);
    },
  );

  ipcMain.handle(
    'dialog:save',
    async (_event, options: Electron.SaveDialogOptions) => {
      const win = BrowserWindow.getFocusedWindow();
      if (!win) return { canceled: true, filePath: undefined };
      return dialog.showSaveDialog(win, options);
    },
  );

  // ─── Shell (permission-gated) ─────────────────────────────────

  ipcMain.handle(
    'shell:exec',
    async (_event, command: string, cwd?: string) => {
      await permissions.require('shell.execute', command);

      return new Promise<{ stdout: string; stderr: string; exitCode: number }>(
        (resolve) => {
          exec(
            command,
            {
              cwd: cwd || os.homedir(),
              timeout: 30_000,
              maxBuffer: 10 * 1024 * 1024,
            },
            (error, stdout, stderr) => {
              resolve({
                stdout: stdout || '',
                stderr: stderr || '',
                exitCode: error?.code ?? 0,
              });
            },
          );
        },
      );
    },
  );

  // ─── Clipboard ────────────────────────────────────────────────

  ipcMain.handle('clipboard:read', async () => {
    await permissions.require('clipboard.read');
    return clipboard.readText();
  });

  ipcMain.handle('clipboard:write', async (_event, text: string) => {
    await permissions.require('clipboard.write');
    clipboard.writeText(text);
  });

  // ─── System Info ──────────────────────────────────────────────

  ipcMain.handle('system:info', () => ({
    platform: os.platform(),
    arch: os.arch(),
    cpus: os.cpus().length,
    totalMemory: os.totalmem(),
    freeMemory: os.freemem(),
    hostname: os.hostname(),
  }));

  // ─── Notifications ────────────────────────────────────────────

  ipcMain.handle(
    'notification:show',
    async (_event, title: string, body: string) => {
      await permissions.require('notification');
      new Notification({ title, body }).show();
    },
  );

  // ─── Permissions ──────────────────────────────────────────────

  ipcMain.handle('permissions:get', () => permissions.getAll());

  ipcMain.handle(
    'permissions:request',
    async (_event, permission: string, reason: string) => {
      return permissions.request(permission, reason);
    },
  );

  // ─── Updates ──────────────────────────────────────────────────

  ipcMain.handle('update:check', () => updater.checkForUpdates());

  ipcMain.handle('update:download', () => updater.downloadUpdate());

  ipcMain.handle('update:install', () => updater.installUpdate());

  ipcMain.handle('update:snooze', (_event, durationMs: number) => {
    return updater.snooze(durationMs);
  });

  ipcMain.handle('update:status', () => updater.getStatus());

  // ─── Auth Window ────────────────────────────────────────────────
  //
  // Opens the user's real system browser (Chrome / Safari / Firefox)
  // for sign-in.  Providers like Google block embedded Electron
  // windows, but they cannot block the user's default browser.
  // A native dialog waits for the user to confirm they have finished.

  ipcMain.handle('browser:open-auth-window', async (_event, url: string) => {
    await shell.openExternal(url);

    const parent = BrowserWindow.getFocusedWindow() ?? undefined;
    const { response } = await dialog.showMessageBox(parent!, {
      type: 'info',
      title: 'Sign in',
      message: 'A sign-in page has been opened in your browser.',
      detail:
        'Please complete the sign-in in your browser, then click "Done" to continue.\n\n' +
        'If you cancelled or could not sign in, click "Cancel".',
      buttons: ['Done', 'Cancel'],
      defaultId: 0,
      cancelId: 1,
    });

    return { success: response === 0 };
  });

  ipcMain.handle('shell:open-external', async (_event, url: string) => {
    await shell.openExternal(url);
  });

  // ─── Browser: inject cookies into the webview partition ────────
  //
  // The webview uses partition="persist:nls-agent".  CDP cannot
  // reliably set cookies on Electron partitioned sessions, so we
  // use Electron's native session.cookies API instead.

  ipcMain.handle(
    'browser:set-cookies',
    async (_event, cookies: Array<Record<string, unknown>>) => {
      const ses = session.fromPartition('persist:nls-agent');
      let ok = 0;
      let fail = 0;
      for (const c of cookies) {
        try {
          const domain = (c.domain as string) || '';
          // Force SameSite=None so cookies survive Electron's
          // third-party classification (all cookies are third-party
          // in an Electron webview).
          await ses.cookies.set({
            url: `https://${domain.replace(/^\./, '')}${c.path || '/'}`,
            name: (c.name as string) || '',
            value: (c.value as string) || '',
            domain,
            path: (c.path as string) || '/',
            secure: true,
            httpOnly: !!(c.httpOnly ?? c.http_only),
            sameSite: 'no_restriction' as const,
            expirationDate:
              typeof c.expires === 'number' && c.expires > 0
                ? c.expires
                : undefined,
          });
          ok++;
        } catch {
          fail++;
        }
      }
      return { ok, fail };
    },
  );

  // ─── MCP (stubs -- Phase 5) ───────────────────────────────────

  ipcMain.handle('mcp:list', () => []);
  ipcMain.handle('mcp:add', async () => {});
  ipcMain.handle('mcp:remove', async () => {});
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function testConnection(url: string): Promise<{ ok: boolean; message: string; latency: number }> {
  const MAX_RETRIES = 3;
  const RETRY_DELAY = 1_000;

  for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
    const result = await testConnectionOnce(url);
    if (result.ok) return result;

    // Retry on DNS failures (cold cache); give up immediately on other errors
    const isDnsError = result.message.includes('ENOTFOUND') || result.message.includes('EAI_AGAIN');
    if (!isDnsError || attempt === MAX_RETRIES) return result;

    await new Promise((r) => setTimeout(r, RETRY_DELAY));
  }

  return { ok: false, message: 'Connection failed after retries', latency: 0 };
}

function testConnectionOnce(url: string): Promise<{ ok: boolean; message: string; latency: number }> {
  return new Promise((resolve) => {
    const start = Date.now();
    const target = url.endsWith('/health') ? url : `${url}/health`;

    const req = http.get(target, { timeout: 5_000 }, (res) => {
      const latency = Date.now() - start;
      if (res.statusCode === 200) {
        resolve({ ok: true, message: 'Connected', latency });
      } else {
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
