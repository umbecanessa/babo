/**
 * Bundled llmfit binary — downloaded into userData on setup and kept in sync
 * on startup via VenvManager.checkDepsSync().
 */

import { app } from 'electron';
import { execFile } from 'child_process';
import * as fs from 'fs';
import * as http from 'http';
import * as https from 'https';
import * as os from 'os';
import * as path from 'path';
import { promisify } from 'util';

const execFileAsync = promisify(execFile);

/** Pin to a known GitHub release; bump when updating Babo's model-fit integration. */
export const LLMFIT_VERSION = '0.9.30';

export interface LlmfitInstallHooks {
  onStatus?: (message: string, progress: number) => void;
  onLog?: (level: 'stdout' | 'stderr', message: string) => void;
}

function standaloneDir(): string {
  return path.join(app.getPath('userData'), 'llmfit-standalone');
}

function binDir(): string {
  return path.join(standaloneDir(), 'bin');
}

function setupStatePath(): string {
  return path.join(app.getPath('userData'), 'setup-state.json');
}

function loadInstalledVersion(): string | null {
  try {
    if (!fs.existsSync(setupStatePath())) return null;
    const data = JSON.parse(fs.readFileSync(setupStatePath(), 'utf-8'));
    return typeof data.llmfitVersion === 'string' ? data.llmfitVersion : null;
  } catch {
    return null;
  }
}

function saveInstalledVersion(version: string): void {
  try {
    let data: Record<string, unknown> = {};
    if (fs.existsSync(setupStatePath())) {
      data = JSON.parse(fs.readFileSync(setupStatePath(), 'utf-8'));
    }
    data.llmfitVersion = version;
    fs.writeFileSync(setupStatePath(), JSON.stringify(data), 'utf-8');
  } catch {
    /* non-critical */
  }
}

/** Path to Babo-managed llmfit, or null if not installed. */
export function getLlmfitBin(): string | null {
  const isWin = os.platform() === 'win32';
  const bin = path.join(binDir(), isWin ? 'llmfit.exe' : 'llmfit');
  return fs.existsSync(bin) ? bin : null;
}

function releaseAssetName(): string | null {
  const ver = LLMFIT_VERSION;
  const platform = os.platform();
  const arch = os.arch();

  const map: Record<string, string> = {
    'win32-x64': `llmfit-v${ver}-x86_64-pc-windows-msvc.zip`,
    'win32-arm64': `llmfit-v${ver}-aarch64-pc-windows-msvc.zip`,
    'darwin-x64': `llmfit-v${ver}-x86_64-apple-darwin.tar.gz`,
    'darwin-arm64': `llmfit-v${ver}-aarch64-apple-darwin.tar.gz`,
    'linux-x64': `llmfit-v${ver}-x86_64-unknown-linux-gnu.tar.gz`,
    'linux-arm64': `llmfit-v${ver}-aarch64-unknown-linux-gnu.tar.gz`,
  };
  return map[`${platform}-${arch}`] ?? null;
}

function downloadFile(
  url: string,
  dest: string,
  onProgress?: (percent: number) => void,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const doRequest = (currentUrl: string, redirectCount = 0) => {
      if (redirectCount > 10) {
        reject(new Error('Too many redirects'));
        return;
      }
      const mod = currentUrl.startsWith('https') ? https : http;
      mod
        .get(currentUrl, (response) => {
          const sc = response.statusCode ?? 0;
          if ((sc === 301 || sc === 302 || sc === 307) && response.headers.location) {
            response.resume();
            doRequest(response.headers.location, redirectCount + 1);
            return;
          }
          if (sc !== 200) {
            response.resume();
            reject(new Error(`Download failed: HTTP ${sc}`));
            return;
          }
          const totalSize = parseInt(response.headers['content-length'] || '0', 10);
          let downloaded = 0;
          const file = fs.createWriteStream(dest);
          response.on('data', (chunk: Buffer) => {
            downloaded += chunk.length;
            if (totalSize > 0 && onProgress) {
              onProgress(Math.min(100, Math.floor((downloaded / totalSize) * 100)));
            }
          });
          response.on('end', () => file.end());
          file.on('finish', () => resolve());
          file.on('error', reject);
        })
        .on('error', reject);
    };
    doRequest(url);
  });
}

async function execTar(args: string[], timeout = 120_000): Promise<void> {
  await execFileAsync('tar', args, { timeout, windowsHide: true });
}

function findLlmfitBinary(root: string): string | null {
  const names = new Set(['llmfit', 'llmfit.exe']);
  const stack = [root];
  while (stack.length) {
    const dir = stack.pop()!;
    let entries: fs.Dirent[];
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const e of entries) {
      const full = path.join(dir, e.name);
      if (e.isDirectory()) {
        stack.push(full);
      } else if (names.has(e.name)) {
        return full;
      }
    }
  }
  return null;
}

async function removeDir(dir: string): Promise<void> {
  if (!fs.existsSync(dir)) return;
  try {
    fs.rmSync(dir, { recursive: true, force: true, maxRetries: 3, retryDelay: 500 });
  } catch {
    /* best-effort */
  }
}

/**
 * Install or upgrade llmfit. Non-fatal on failure (heuristic model fit still works).
 * Returns path to binary when successful.
 */
export async function ensureLlmfit(hooks?: LlmfitInstallHooks): Promise<string | null> {
  const log = (level: 'stdout' | 'stderr', message: string) => {
    hooks?.onLog?.(level, message);
  };
  const status = (message: string, progress: number) => {
    hooks?.onStatus?.(message, progress);
  };

  const installed = loadInstalledVersion();
  const bin = getLlmfitBin();
  if (bin && installed === LLMFIT_VERSION) {
    log('stdout', `llmfit already installed (${LLMFIT_VERSION})`);
    return bin;
  }

  const asset = releaseAssetName();
  if (!asset) {
    log(
      'stderr',
      `No llmfit build for ${os.platform()}-${os.arch()} (model fit will use estimates)`,
    );
    return bin;
  }

  const base = `https://github.com/AlexsJones/llmfit/releases/download/v${LLMFIT_VERSION}`;
  const url = `${base}/${asset}`;
  const root = standaloneDir();
  const staging = path.join(root, 'staging');
  const isZip = asset.endsWith('.zip');
  const archivePath = path.join(root, isZip ? 'llmfit.zip' : 'llmfit.tar.gz');

  const binStaging = path.join(staging, 'bin-next');
  try {
    await removeDir(staging);
    fs.mkdirSync(root, { recursive: true });
    fs.mkdirSync(staging, { recursive: true });
    await removeDir(binStaging);
    fs.mkdirSync(binStaging, { recursive: true });

    status('Downloading llmfit (model fit tool)...', 96);
    log('stdout', `Downloading llmfit v${LLMFIT_VERSION}...`);

    await downloadFile(url, archivePath, (pct) => {
      status(`Downloading llmfit... ${pct}%`, 96 + Math.floor(pct * 0.02));
    });

    status('Extracting llmfit...', 98);
    const extractRoot = path.join(staging, 'extract');
    await removeDir(extractRoot);
    fs.mkdirSync(extractRoot, { recursive: true });
    if (isZip) {
      await execTar(['xf', archivePath, '-C', extractRoot]);
    } else {
      await execTar(['xzf', archivePath, '-C', extractRoot]);
    }
    try {
      fs.unlinkSync(archivePath);
    } catch { /* ignore */ }

    const found = findLlmfitBinary(extractRoot);
    if (!found) {
      throw new Error('llmfit binary not found in release archive');
    }

    const destName = os.platform() === 'win32' ? 'llmfit.exe' : 'llmfit';
    const destStaging = path.join(binStaging, destName);
    fs.copyFileSync(found, destStaging);
    if (os.platform() !== 'win32') {
      fs.chmodSync(destStaging, 0o755);
    }

    if (os.platform() === 'darwin') {
      try {
        await execFileAsync('xattr', ['-dr', 'com.apple.quarantine', destStaging], {
          timeout: 30_000,
        });
      } catch { /* non-critical */ }
    }

    await removeDir(binDir());
    fs.mkdirSync(binDir(), { recursive: true });
    fs.renameSync(destStaging, path.join(binDir(), destName));
    await removeDir(staging);
    saveInstalledVersion(LLMFIT_VERSION);

    const out = getLlmfitBin();
    if (out) {
      log('stdout', `llmfit installed at ${out}`);
      status('llmfit ready', 99);
      return out;
    }
    throw new Error('llmfit install completed but binary missing');
  } catch (err: unknown) {
    const msg = (err as Error)?.message || String(err);
    log('stderr', `llmfit install failed: ${msg} (model fit will use estimates)`);
    await removeDir(staging);
    try {
      fs.unlinkSync(archivePath);
    } catch { /* ignore */ }
    return getLlmfitBin();
  }
}
