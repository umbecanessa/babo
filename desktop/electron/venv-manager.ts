/**
 * NLS Desktop App -- Python Virtual Environment Manager
 *
 * Handles first-run setup: detects system Python, creates a venv,
 * and installs NLS dependencies.  Progress is streamed to the
 * renderer via IPC events.
 */

import { app, BrowserWindow } from 'electron';
import { ChildProcess, spawn, execFile } from 'child_process';
import * as crypto from 'crypto';
import * as fs from 'fs';
import * as https from 'https';
import * as http from 'http';
import * as path from 'path';
import * as os from 'os';
import { ensureLlmfit, getLlmfitBin, LLMFIT_VERSION } from './llmfit-installer';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface SetupStatus {
  stage: 'idle' | 'checking' | 'creating-venv' | 'installing' | 'ready' | 'error';
  message: string;
  progress: number; // 0-100
  pythonPath: string | null;
  venvPath: string | null;
  error: string | null;
}

// ---------------------------------------------------------------------------
// VenvManager
// ---------------------------------------------------------------------------

export class VenvManager {
  private venvPath: string;
  private nlsRoot: string;
  private requirementsPath: string;
  private setupStatePath: string;
  private setupLogPath: string;
  private logStream: fs.WriteStream | null = null;
  private _status: SetupStatus;

  constructor() {
    this.venvPath = path.join(app.getPath('userData'), 'python-env');
    // NLS source root: two levels up from dist-electron/ in dev,
    // or from extraResources in production
    this.nlsRoot = app.isPackaged
      ? path.join(process.resourcesPath, 'nls-source')
      : path.resolve(__dirname, '..', '..');
    this.requirementsPath = path.join(this.nlsRoot, 'requirements-desktop.txt');
    this.setupStatePath = path.join(app.getPath('userData'), 'setup-state.json');
    this.setupLogPath = path.join(app.getPath('userData'), 'setup.log');
    this._status = {
      stage: 'idle',
      message: '',
      progress: 0,
      pythonPath: null,
      venvPath: null,
      error: null,
    };
  }

  get status(): SetupStatus {
    return { ...this._status };
  }

  /**
   * Check if the venv is already set up and ready.
   */
  isReady(): boolean {
    const pythonBin = this.getVenvPython();
    return fs.existsSync(pythonBin);
  }

  /**
   * Get the path to the Python binary inside the venv.
   */
  getVenvPython(): string {
    const isWin = os.platform() === 'win32';
    return isWin
      ? path.join(this.venvPath, 'Scripts', 'python.exe')
      : path.join(this.venvPath, 'bin', 'python');
  }

  /**
   * Get the NLS source root directory.
   */
  getNlsRoot(): string {
    return this.nlsRoot;
  }

  /**
   * Compare the current requirements-desktop.txt against the hash stored
   * at first install.  If it changed (e.g. after an app auto-update),
   * re-run pip install so the venv stays in sync.  No-ops if the venv
   * doesn't exist yet or the hash hasn't changed.
   */
  async checkDepsSync(): Promise<void> {
    if (!this.isReady()) return;
    if (!fs.existsSync(this.requirementsPath)) return;

    const currentHash = this.hashFile(this.requirementsPath);
    const savedHash = this.loadRequirementsHash();

    if (currentHash !== savedHash) {
      this.updateStatus('installing', 'Updating dependencies after app update...', 10);
      try {
        await this.runInVenv(
          ['-m', 'pip', 'install', '--verbose', '-r', this.requirementsPath],
          (line: string) => {
            if (line.includes('Collecting') || line.includes('Installing')) {
              const summary = this.summarizeInstallLine(line);
              if (summary) {
                const progress = Math.min(80, this._status.progress + 1);
                this.updateStatus('installing', summary, progress);
              }
            }
          },
        );
        this.saveRequirementsHash(currentHash);

        // New pip packages may require post-install setup (e.g. playwright browsers)
        this.updateStatus('installing', 'Installing browser engine...', 82);
        await this.installBrowserEngine();

        // Visual model prefetch only when user enables ambient vision (see prefetchVisionModel)
      } catch (err: any) {
        this.broadcastLog('stderr', `Dependency sync failed: ${err.message}`);
      }
    }

    // Ensure CUDA-enabled PyTorch when an NVIDIA GPU is present.
    // The default PyPI torch wheel is CPU-only, which disables local VLM
    // inference even on machines with a capable GPU.
    await this.ensureTorchCuda();

    // Ensure Node.js runtime is available (added after initial release,
    // so existing installs that ran setup() before this feature need it too)
    if (!this.getNodeBin()) {
      try {
        await this.ensureStandaloneNode();
      } catch (err: any) {
        this.broadcastLog('stderr', `Node.js setup failed: ${err.message}`);
      }
    }

    // Ensure PowerShell 7 for Windows agent shell (bash() maps to pwsh)
    if (os.platform() === 'win32' && !this.getPwshBin()) {
      try {
        await this.ensureStandalonePowerShell();
      } catch (err: any) {
        this.broadcastLog('stderr', `PowerShell setup failed: ${err.message}`);
      }
    }

    await this.ensureLlmfitTool();

    this.updateStatus('ready', 'Dependencies up to date', 100);
  }

  /**
   * Run the full setup: detect Python, create venv, install deps.
   */
  /**
   * Pre-download Moondream/SmolVLM weights (call when user enables ambient vision).
   */
  async prefetchVisionModel(options?: { quiet?: boolean }): Promise<void> {
    if (!this.isReady()) {
      throw new Error('Python environment not ready');
    }
    const quiet = options?.quiet ?? false;
    if (!quiet) {
      this.updateStatus('installing', 'Downloading visual model (Moondream)...', 85);
    } else {
      this.broadcastVisionPrefetch('Downloading screen awareness model…', 0);
    }
    await this.prefetchVisualModel(quiet);
    if (!quiet) {
      await this.ensureTorchCuda();
      this.updateStatus('ready', 'Visual model ready', 100);
    } else {
      this.broadcastVisionPrefetch('Screen awareness model ready', 100);
    }
  }

  async setup(options?: { prefetchVision?: boolean }): Promise<void> {
    const prefetchVision = options?.prefetchVision ?? false;
    try {
      this.logStream = fs.createWriteStream(this.setupLogPath, { flags: 'a' });
      this.logStream.write(
        `\n${'='.repeat(60)}\n` +
        `Setup started at ${new Date().toISOString()}\n` +
        `Platform: ${os.platform()} ${os.arch()} | Node: ${process.version}\n` +
        `${'='.repeat(60)}\n`,
      );
    } catch { /* best-effort */ }

    try {
      // Step 1: Detect system Python
      this.updateStatus('checking', 'Detecting Python installation...', 5);
      const systemPython = await this.detectPython();
      if (!systemPython) {
        throw new Error(
          'Could not find or download Python 3.11+. ' +
          'Please check your internet connection, or install Python manually from ' +
          'https://python.org (ensure "Add Python to PATH" is checked during installation).',
        );
      }
      this.updateStatus('checking', `Found Python: ${systemPython}`, 15);

      // Step 2: Create virtual environment
      this.updateStatus('creating-venv', 'Creating virtual environment...', 20);
      await this.createVenv(systemPython);
      this.updateStatus('creating-venv', 'Virtual environment created', 35);

      // Step 3: Upgrade pip
      this.updateStatus('installing', 'Upgrading pip...', 40);
      await this.runInVenv(['-m', 'pip', 'install', '--upgrade', 'pip']);

      // Step 4: Install dependencies
      this.updateStatus('installing', 'Installing NLS dependencies...', 45);
      await this.installDeps();

      // Step 5: Install browser engine (Chromium + stealth extensions)
      this.updateStatus('installing', 'Installing browser engine (Chromium)...', 82);
      await this.installBrowserEngine();

      if (prefetchVision) {
        this.updateStatus('installing', 'Downloading visual model (Moondream)...', 85);
        await this.prefetchVisualModel();
      }

      // Step 6/7: Upgrade to CUDA-enabled PyTorch if an NVIDIA GPU is present
      this.updateStatus('installing', 'Checking GPU for CUDA PyTorch...', 88);
      await this.ensureTorchCuda();

      // Step 8: Ensure standalone Node.js (for WhatsApp/Telegram bridges)
      this.updateStatus('installing', 'Setting up Node.js runtime...', 92);
      await this.ensureStandaloneNode();

      // Step 9: Ensure standalone PowerShell 7 (Windows agent shell)
      if (os.platform() === 'win32') {
        this.updateStatus('installing', 'Setting up PowerShell 7...', 95);
        await this.ensureStandalonePowerShell();
      }

      // Step 10: llmfit for hardware-aware model recommendations (Model Fit)
      this.updateStatus('installing', 'Installing llmfit (model recommendations)...', 96);
      await this.ensureLlmfitTool();

      // Step 11: Ensure data directories
      this.updateStatus('installing', 'Setting up data directories...', 98);
      this.ensureDataDirs();

      // Done
      this._status.pythonPath = this.getVenvPython();
      this._status.venvPath = this.venvPath;
      this.updateStatus('ready', 'Setup complete', 100);
      this.saveSetupState();
    } catch (err: any) {
      this.updateStatus('error', err.message, 0, err.message);
      throw err;
    } finally {
      try { this.logStream?.end(); this.logStream = null; } catch { /* best-effort */ }
    }
  }

  /**
   * Reset the venv (delete and re-create on next setup).
   */
  async reset(): Promise<void> {
    await this.removeDirRobust(this.venvPath);
    if (fs.existsSync(this.setupStatePath)) {
      fs.unlinkSync(this.setupStatePath);
    }
    this._status = {
      stage: 'idle',
      message: 'Reset complete -- run setup again',
      progress: 0,
      pythonPath: null,
      venvPath: null,
      error: null,
    };
  }

  // ─── Internal ─────────────────────────────────────────────────

  private async detectPython(): Promise<string | null> {
    // Phase 1: Try standard commands on PATH
    const fromPath = await this.detectPythonFromPath();
    if (fromPath) return fromPath;

    // Phase 2: Scan well-known installation directories
    const fromKnown = await this.detectPythonFromKnownPaths();
    if (fromKnown) return fromKnown;

    // Phase 3: Download a standalone Python distribution
    return this.ensureStandalonePython();
  }

  private async detectPythonFromPath(): Promise<string | null> {
    interface Candidate { cmd: string; args: string[] }

    const candidates: Candidate[] =
      os.platform() === 'win32'
        ? [
            { cmd: 'python', args: ['--version'] },
            { cmd: 'python3', args: ['--version'] },
            { cmd: 'py', args: ['-3', '--version'] },
          ]
        : [
            { cmd: 'python3', args: ['--version'] },
            { cmd: 'python', args: ['--version'] },
          ];

    for (const { cmd, args } of candidates) {
      try {
        const version = await this.execSimple(cmd, args);
        if (!this.isPython311OrNewer(version)) continue;

        if (cmd === 'py') {
          try {
            const resolved = (
              await this.execSimple('py', ['-3', '-c', 'import sys; print(sys.executable)'])
            ).trim();
            if (resolved && fs.existsSync(resolved)) return resolved;
          } catch { /* fall through */ }
          return 'py -3';
        }

        return cmd;
      } catch {
        // Try next candidate
      }
    }
    return null;
  }

  private async detectPythonFromKnownPaths(): Promise<string | null> {
    const pythonPaths: string[] = [];

    if (os.platform() === 'win32') {
      const localAppData = process.env['LOCALAPPDATA'] || '';
      const programFiles = process.env['ProgramFiles'] || 'C:\\Program Files';
      const systemRoot = process.env['SystemRoot'] || 'C:\\Windows';

      for (const minor of [14, 13, 12, 11]) {
        if (localAppData) {
          pythonPaths.push(
            path.join(localAppData, 'Programs', 'Python', `Python3${minor}`, 'python.exe'),
          );
        }
        pythonPaths.push(
          path.join(programFiles, `Python3${minor}`, 'python.exe'),
          `C:\\Python3${minor}\\python.exe`,
        );
      }
      pythonPaths.push(path.join(systemRoot, 'py.exe'));
    } else if (os.platform() === 'darwin') {
      pythonPaths.push('/opt/homebrew/bin/python3', '/usr/local/bin/python3');
      for (const minor of [14, 13, 12, 11]) {
        pythonPaths.push(
          `/Library/Frameworks/Python.framework/Versions/3.${minor}/bin/python3`,
        );
      }
    } else {
      pythonPaths.push('/usr/local/bin/python3', '/usr/bin/python3');
    }

    for (const p of pythonPaths) {
      if (!fs.existsSync(p)) continue;
      try {
        if (path.basename(p) === 'py.exe') {
          const version = await this.execSimple(p, ['-3', '--version']);
          if (!this.isPython311OrNewer(version)) continue;
          const resolved = (
            await this.execSimple(p, ['-3', '-c', 'import sys; print(sys.executable)'])
          ).trim();
          if (resolved && fs.existsSync(resolved)) return resolved;
          continue;
        }
        const version = await this.execSimple(p, ['--version']);
        if (this.isPython311OrNewer(version)) return p;
      } catch {
        // Try next
      }
    }
    return null;
  }

  private isPython311OrNewer(versionOutput: string): boolean {
    const match = versionOutput.match(/Python (\d+)\.(\d+)/);
    if (!match) return false;
    const major = parseInt(match[1], 10);
    const minor = parseInt(match[2], 10);
    return major === 3 && minor >= 11;
  }

  // ─── Standalone Python (auto-download) ─────────────────────────

  private static readonly STANDALONE_PYTHON_VERSION = '3.12.12';
  private static readonly STANDALONE_PYTHON_TAG = '20260211';

  private getStandalonePythonDir(): string {
    return path.join(app.getPath('userData'), 'python-standalone');
  }

  private getStandalonePythonBin(): string {
    const dir = this.getStandalonePythonDir();
    return os.platform() === 'win32'
      ? path.join(dir, 'python', 'python.exe')
      : path.join(dir, 'python', 'bin', 'python3');
  }

  private getStandalonePythonUrl(): string | null {
    const ver = VenvManager.STANDALONE_PYTHON_VERSION;
    const tag = VenvManager.STANDALONE_PYTHON_TAG;
    const base = `https://github.com/astral-sh/python-build-standalone/releases/download/${tag}`;
    const triples: Record<string, string> = {
      'win32-x64':   'x86_64-pc-windows-msvc',
      'win32-arm64': 'aarch64-pc-windows-msvc',
      'darwin-x64':  'x86_64-apple-darwin',
      'darwin-arm64': 'aarch64-apple-darwin',
      'linux-x64':   'x86_64-unknown-linux-gnu',
      'linux-arm64':  'aarch64-unknown-linux-gnu',
    };
    const triple = triples[`${os.platform()}-${os.arch()}`];
    if (!triple) return null;
    return `${base}/cpython-${ver}+${tag}-${triple}-install_only_stripped.tar.gz`;
  }

  private async ensureStandalonePython(): Promise<string | null> {
    const pythonBin = this.getStandalonePythonBin();
    if (fs.existsSync(pythonBin)) {
      this.broadcastLog('stdout', `Using standalone Python at ${pythonBin}`);
      return pythonBin;
    }

    const url = this.getStandalonePythonUrl();
    if (!url) {
      this.broadcastLog(
        'stderr',
        `No standalone Python build available for ${os.platform()}-${os.arch()}`,
      );
      return null;
    }

    const standaloneDir = this.getStandalonePythonDir();
    fs.mkdirSync(standaloneDir, { recursive: true });
    const archivePath = path.join(standaloneDir, 'python.tar.gz');

    try {
      this.broadcastLog('stdout', `Downloading Python from ${url}...`);
      this.updateStatus('checking', 'Downloading Python runtime...', 6);

      await this.downloadFile(url, archivePath, (percent) => {
        this.updateStatus(
          'checking',
          `Downloading Python runtime... ${percent}%`,
          6 + Math.floor(percent * 0.07),
        );
      });

      this.updateStatus('checking', 'Extracting Python runtime...', 13);
      this.broadcastLog('stdout', 'Extracting Python...');
      await this.execSimple('tar', ['xzf', archivePath, '-C', standaloneDir], 120_000);

      try { fs.unlinkSync(archivePath); } catch { /* non-critical */ }

      if (os.platform() !== 'win32' && fs.existsSync(pythonBin)) {
        fs.chmodSync(pythonBin, 0o755);
      }
      if (os.platform() === 'darwin') {
        try {
          await this.execSimple('xattr', ['-dr', 'com.apple.quarantine', standaloneDir]);
        } catch { /* non-critical */ }
      }

      if (fs.existsSync(pythonBin)) {
        this.broadcastLog('stdout', `Standalone Python installed at ${pythonBin}`);
        return pythonBin;
      }

      this.broadcastLog('stderr', 'Python extraction succeeded but binary not found at expected path');
      return null;
    } catch (err: any) {
      this.broadcastLog('stderr', `Failed to download/extract Python: ${err.message}`);
      try { fs.unlinkSync(archivePath); } catch { /* clean up */ }
      try { fs.rmSync(standaloneDir, { recursive: true, force: true }); } catch { /* clean up */ }
      return null;
    }
  }

  private downloadFile(
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

        const mod: typeof https | typeof http =
          currentUrl.startsWith('https') ? https : http;

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
                onProgress(Math.round((downloaded / totalSize) * 100));
              }
            });

            response.pipe(file);
            file.on('finish', () => file.close(() => resolve()));
            file.on('error', (err) => {
              file.close();
              try { fs.unlinkSync(dest); } catch { /* ignore */ }
              reject(err);
            });
          })
          .on('error', reject);
      };

      doRequest(url);
    });
  }

  private async createVenv(systemPython: string): Promise<void> {
    // Remove existing venv if corrupted
    if (fs.existsSync(this.venvPath)) {
      const pythonBin = this.getVenvPython();
      if (!fs.existsSync(pythonBin)) {
        await this.removeDirRobust(this.venvPath);
      } else {
        return; // Venv already exists and looks valid
      }
    }

    const baseArgs = systemPython.startsWith('py ')
      ? ['-3', '-m', 'venv']
      : ['-m', 'venv'];
    const cmd = systemPython.startsWith('py ') ? 'py' : systemPython;

    try {
      await this.execSimple(cmd, [...baseArgs, this.venvPath]);
    } catch (firstErr: any) {
      this.broadcastLog('stderr', `venv creation failed: ${firstErr.message}`);
      this.broadcastLog('stdout', 'Retrying with --without-pip...');

      await this.removeDirRobust(this.venvPath);

      // Fallback: create venv without pip (ensurepip is often broken on
      // Windows Store Python and pre-release versions), then bootstrap pip.
      try {
        await this.execSimple(cmd, [...baseArgs, '--without-pip', this.venvPath]);
        await this.bootstrapPip();
      } catch (secondErr: any) {
        await this.removeDirRobust(this.venvPath);
        throw new Error(
          `Failed to create virtual environment.\n` +
          `Attempt 1: ${firstErr.message}\n` +
          `Attempt 2 (--without-pip): ${secondErr.message}`,
        );
      }
    }
  }

  private async bootstrapPip(): Promise<void> {
    const pythonBin = this.getVenvPython();

    // Try ensurepip first (faster, no download)
    try {
      await this.execSimple(pythonBin, ['-m', 'ensurepip', '--upgrade']);
      return;
    } catch {
      this.broadcastLog('stdout', 'ensurepip unavailable, downloading get-pip.py...');
    }

    // Fall back to get-pip.py
    const getPipPath = path.join(this.venvPath, 'get-pip.py');
    await this.downloadFile('https://bootstrap.pypa.io/get-pip.py', getPipPath);
    try {
      await this.execSimple(pythonBin, [getPipPath], 120_000);
    } finally {
      try { fs.unlinkSync(getPipPath); } catch { /* non-critical */ }
    }
  }

  private async installDeps(): Promise<void> {
    if (!fs.existsSync(this.requirementsPath)) {
      throw new Error(
        `Requirements file not found: ${this.requirementsPath}`,
      );
    }

    await this.runInVenv(
      ['-m', 'pip', 'install', '--verbose', '-r', this.requirementsPath],
      (line: string) => {
        if (
          line.includes('Collecting') ||
          line.includes('Installing') ||
          line.includes('Successfully installed')
        ) {
          const summary = this.summarizeInstallLine(line);
          if (summary) {
            const progress = Math.min(84, this._status.progress + 1);
            this.updateStatus('installing', summary, progress);
          }
        }
      },
    );
  }

  private async installBrowserEngine(): Promise<void> {
    // Prefer Playwright CLI (browser_use has no __main__ on some versions).
    const onLine = (line: string) => {
      const summary = this.summarizeInstallLine(line);
      if (summary) {
        this.updateStatus('installing', summary, 84);
      }
    };

    try {
      await this.runInVenv(
        ['-m', 'playwright', 'install', 'chromium'],
        onLine,
      );
      return;
    } catch {
      this.broadcastLog('stdout', 'Playwright install failed, trying browser-use…');
    }

    try {
      await this.runInVenv(['-m', 'browser_use', 'install'], onLine);
    } catch (err: any) {
      console.warn('Browser engine install failed:', err.message);
      this.broadcastLog('stderr', `Browser engine install failed: ${err.message}`);
    }
  }

  /**
   * Detect whether an NVIDIA GPU is present and, if so, swap out the
   * CPU-only PyTorch wheel (installed by default from PyPI) for the
   * CUDA-enabled variant from the PyTorch CUDA index.
   *
   * This is idempotent — if CUDA torch is already installed the quick
   * Python probe returns True and we skip the 2+ GB reinstall.
   */
  private async ensureTorchCuda(): Promise<void> {
    // Quick check: is torch CUDA already working?
    try {
      const result = await this.runInVenv([
        '-c',
        'import torch; print("ok" if torch.cuda.is_available() else "cpu")',
      ]);
      if (result.trim() === 'ok') {
        this.broadcastLog('stdout', 'PyTorch CUDA already active — skipping GPU torch install');
        return;
      }
    } catch {
      // torch not installed yet — will be handled by deps install
      return;
    }

    // Probe for NVIDIA GPU via nvidia-smi
    let hasNvidiaGpu = false;
    try {
      const smi = await this.execSimple('nvidia-smi', ['--query-gpu=name', '--format=csv,noheader'], 8_000);
      hasNvidiaGpu = smi.trim().length > 0;
    } catch {
      // nvidia-smi not found → no NVIDIA GPU
    }

    if (!hasNvidiaGpu) {
      this.broadcastLog('stdout', 'No NVIDIA GPU detected — keeping CPU PyTorch');
      return;
    }

    this.broadcastLog('stdout', 'NVIDIA GPU found but torch is CPU-only — upgrading to CUDA PyTorch...');
    this.updateStatus('installing', 'Installing CUDA-enabled PyTorch (this may take a few minutes)...', 89);

    try {
      await this.runInVenv(
        [
          '-m', 'pip', 'install', '--force-reinstall',
          'torch', 'torchvision',
          '--index-url', 'https://download.pytorch.org/whl/cu128',
        ],
        (line: string) => {
          if (line.includes('Downloading') || line.includes('Installing') || line.includes('Successfully')) {
            const summary = this.summarizeInstallLine(line);
            if (summary) {
              this.updateStatus('installing', summary, 89);
            }
          }
        },
      );
      this.broadcastLog('stdout', 'CUDA PyTorch installed successfully');

      // Verify
      try {
        const check = await this.runInVenv([
          '-c',
          'import torch; v = torch.__version__; c = torch.cuda.is_available(); print(f"{v} cuda={c}")',
        ]);
        this.broadcastLog('stdout', `PyTorch after CUDA install: ${check.trim()}`);
      } catch {
        // non-critical verification failure
      }
    } catch (err: any) {
      this.broadcastLog('stderr', `CUDA PyTorch install failed (falling back to CPU torch): ${err.message}`);
    }
  }

  private async prefetchVisualModel(quiet = false): Promise<void> {
    // Pre-download the Moondream VLM to the HuggingFace cache so the
    // Visual Cortex can load from disk at runtime without a network wait.
    // Non-fatal: if this fails the model will be downloaded on first use.
    try {
      await this.runInVenv(
        ['-m', 'nls.scripts.prefetch_moondream'],
        (line: string) => {
          if (line.startsWith('PREFETCH:downloading:')) {
            const model = line.replace('PREFETCH:downloading:', '');
            const msg = `Downloading ${model}…`;
            if (quiet) {
              this.broadcastVisionPrefetch(msg, 40);
            } else {
              this.updateStatus('installing', `Downloading visual model: ${model}...`, 86);
            }
          } else if (line.startsWith('PREFETCH:done')) {
            if (quiet) {
              this.broadcastVisionPrefetch('Screen awareness model cached', 100);
            } else {
              this.updateStatus('installing', 'Visual model cached', 91);
            }
          } else if (line.startsWith('PREFETCH:error:')) {
            this.broadcastLog('stderr', `Visual model prefetch: ${line.replace('PREFETCH:error:', '')}`);
          } else if (line.startsWith('PREFETCH:skip:')) {
            this.broadcastLog('stdout', `Visual model prefetch skipped: ${line.replace('PREFETCH:skip:', '')}`);
          }
        },
      );
    } catch (err: any) {
      console.warn('Visual model prefetch failed (non-fatal):', err.message);
      this.broadcastLog('stderr', `Visual model prefetch failed: ${err.message}`);
      if (quiet) {
        this.broadcastVisionPrefetch('Will download on first use', 100);
      }
    }
  }

  // ─── Standalone Node.js (auto-download for bridges) ─────────────

  private static readonly STANDALONE_NODE_VERSION = '20.18.3';

  private getStandaloneNodeDir(): string {
    return path.join(app.getPath('userData'), 'node-standalone');
  }

  getNodeBin(): string | null {
    const dir = this.getStandaloneNodeDir();
    const bin = os.platform() === 'win32'
      ? path.join(dir, 'node', 'node.exe')
      : path.join(dir, 'node', 'bin', 'node');
    return fs.existsSync(bin) ? bin : null;
  }

  getNpmBin(): string | null {
    const dir = this.getStandaloneNodeDir();
    const bin = os.platform() === 'win32'
      ? path.join(dir, 'node', 'npm.cmd')
      : path.join(dir, 'node', 'bin', 'npm');
    return fs.existsSync(bin) ? bin : null;
  }

  private getStandaloneNodeUrl(): { url: string; ext: string } | null {
    const ver = VenvManager.STANDALONE_NODE_VERSION;
    const base = `https://nodejs.org/dist/v${ver}`;
    const platform = os.platform();
    const arch = os.arch();

    const targets: Record<string, { file: string; ext: string }> = {
      'win32-x64':    { file: `node-v${ver}-win-x64.zip`,           ext: 'zip' },
      'win32-arm64':  { file: `node-v${ver}-win-arm64.zip`,         ext: 'zip' },
      'darwin-x64':   { file: `node-v${ver}-darwin-x64.tar.gz`,     ext: 'tar.gz' },
      'darwin-arm64': { file: `node-v${ver}-darwin-arm64.tar.gz`,   ext: 'tar.gz' },
      'linux-x64':    { file: `node-v${ver}-linux-x64.tar.gz`,     ext: 'tar.gz' },
      'linux-arm64':  { file: `node-v${ver}-linux-arm64.tar.gz`,   ext: 'tar.gz' },
    };
    const target = targets[`${platform}-${arch}`];
    if (!target) return null;
    return { url: `${base}/${target.file}`, ext: target.ext };
  }

  private async ensureStandaloneNode(): Promise<void> {
    if (this.getNodeBin()) {
      this.broadcastLog('stdout', `Node.js already installed at ${this.getNodeBin()}`);
      return;
    }

    const target = this.getStandaloneNodeUrl();
    if (!target) {
      this.broadcastLog(
        'stderr',
        `No Node.js build available for ${os.platform()}-${os.arch()} (bridges requiring Node will be disabled)`,
      );
      return;
    }

    const standaloneDir = this.getStandaloneNodeDir();
    fs.mkdirSync(standaloneDir, { recursive: true });
    const archiveExt = target.ext === 'zip' ? 'node.zip' : 'node.tar.gz';
    const archivePath = path.join(standaloneDir, archiveExt);

    try {
      this.broadcastLog('stdout', `Downloading Node.js v${VenvManager.STANDALONE_NODE_VERSION}...`);
      this.updateStatus('installing', 'Downloading Node.js runtime...', 91);

      await this.downloadFile(target.url, archivePath, (percent) => {
        this.updateStatus(
          'installing',
          `Downloading Node.js runtime... ${percent}%`,
          91 + Math.floor(percent * 0.03),
        );
      });

      this.updateStatus('installing', 'Extracting Node.js runtime...', 94);
      this.broadcastLog('stdout', 'Extracting Node.js...');

      if (target.ext === 'zip') {
        await this.execSimple('tar', ['xf', archivePath, '-C', standaloneDir], 120_000);
      } else {
        await this.execSimple('tar', ['xzf', archivePath, '-C', standaloneDir], 120_000);
      }

      try { fs.unlinkSync(archivePath); } catch { /* non-critical */ }

      // Rename extracted directory (node-v20.x.x-os-arch) to just "node"
      const ver = VenvManager.STANDALONE_NODE_VERSION;
      const entries = fs.readdirSync(standaloneDir, { withFileTypes: true });
      const nodeDir = entries.find(
        (e) => e.isDirectory() && e.name.startsWith(`node-v${ver}`),
      );
      if (nodeDir) {
        const from = path.join(standaloneDir, nodeDir.name);
        const to = path.join(standaloneDir, 'node');
        fs.renameSync(from, to);
      }

      if (os.platform() === 'darwin') {
        try {
          await this.execSimple('xattr', ['-dr', 'com.apple.quarantine', standaloneDir]);
        } catch { /* non-critical */ }
      }

      if (this.getNodeBin()) {
        this.broadcastLog('stdout', `Node.js installed at ${this.getNodeBin()}`);
      } else {
        this.broadcastLog('stderr', 'Node.js extraction succeeded but binary not found');
      }
    } catch (err: any) {
      this.broadcastLog('stderr', `Failed to install Node.js: ${err.message} (bridges will be disabled)`);
      try { fs.unlinkSync(archivePath); } catch { /* clean up */ }
    }
  }

  // ─── Standalone PowerShell 7 (Windows agent shell) ──────────────

  private static readonly STANDALONE_PWSH_VERSION = '7.5.7';

  private getStandalonePwshDir(): string {
    return path.join(app.getPath('userData'), 'powershell-standalone');
  }

  getPwshBin(): string | null {
    if (os.platform() !== 'win32') return null;
    const bin = path.join(this.getStandalonePwshDir(), 'pwsh', 'pwsh.exe');
    return fs.existsSync(bin) ? bin : null;
  }

  private getStandalonePwshUrl(): { url: string } | null {
    if (os.platform() !== 'win32') return null;

    const ver = VenvManager.STANDALONE_PWSH_VERSION;
    const base = `https://github.com/PowerShell/PowerShell/releases/download/v${ver}`;
    const arch = os.arch();

    const files: Record<string, string> = {
      x64: `PowerShell-${ver}-win-x64.zip`,
      arm64: `PowerShell-${ver}-win-arm64.zip`,
    };
    const file = files[arch];
    if (!file) return null;
    return { url: `${base}/${file}` };
  }

  private async ensureStandalonePowerShell(): Promise<void> {
    if (os.platform() !== 'win32') return;

    if (this.getPwshBin()) {
      this.broadcastLog('stdout', `PowerShell 7 already installed at ${this.getPwshBin()}`);
      return;
    }

    const target = this.getStandalonePwshUrl();
    if (!target) {
      this.broadcastLog(
        'stderr',
        `No PowerShell build available for ${os.platform()}-${os.arch()} (will use system shell)`,
      );
      return;
    }

    const standaloneDir = this.getStandalonePwshDir();
    const pwshDir = path.join(standaloneDir, 'pwsh');
    const archivePath = path.join(standaloneDir, 'powershell.zip');

    try {
      if (fs.existsSync(pwshDir)) {
        await this.removeDirRobust(pwshDir);
      }
      fs.mkdirSync(pwshDir, { recursive: true });

      this.broadcastLog(
        'stdout',
        `Downloading PowerShell v${VenvManager.STANDALONE_PWSH_VERSION}...`,
      );
      this.updateStatus('installing', 'Downloading PowerShell 7...', 95);

      await this.downloadFile(target.url, archivePath, (percent) => {
        this.updateStatus(
          'installing',
          `Downloading PowerShell 7... ${percent}%`,
          95 + Math.floor(percent * 0.015),
        );
      });

      this.updateStatus('installing', 'Extracting PowerShell 7...', 97);
      this.broadcastLog('stdout', 'Extracting PowerShell 7...');

      await this.execSimple('tar', ['xf', archivePath, '-C', pwshDir], 300_000);

      try { fs.unlinkSync(archivePath); } catch { /* non-critical */ }

      if (this.getPwshBin()) {
        this.broadcastLog('stdout', `PowerShell 7 installed at ${this.getPwshBin()}`);
      } else {
        this.broadcastLog('stderr', 'PowerShell extraction succeeded but pwsh.exe not found');
      }
    } catch (err: any) {
      this.broadcastLog(
        'stderr',
        `Failed to install PowerShell 7: ${err.message} (will use system shell)`,
      );
      try { fs.unlinkSync(archivePath); } catch { /* clean up */ }
    }
  }

  /** Bundled llmfit for Model Fit (onboarding + settings). */
  getLlmfitBin(): string | null {
    return getLlmfitBin();
  }

  private async ensureLlmfitTool(): Promise<void> {
    await ensureLlmfit({
      onStatus: (message, progress) => {
        this.updateStatus('installing', message, progress);
      },
      onLog: (level, message) => this.broadcastLog(level, message),
    });
  }

  private ensureDataDirs(): void {
    const dataDir = path.join(app.getPath('userData'), 'data');
    const dirs = [
      path.join(dataDir, 'agents'),
      path.join(dataDir, 'genesis'),
      path.join(dataDir, 'adapters'),
    ];
    for (const dir of dirs) {
      if (!fs.existsSync(dir)) {
        fs.mkdirSync(dir, { recursive: true });
      }
    }

    // Bundled genesis templates (extraResources) — server also seeds on startup.
    const srcGenesis = app.isPackaged
      ? path.join(process.resourcesPath, 'genesis_templates')
      : path.join(this.nlsRoot, 'genesis_templates');
    const destGenesis = path.join(dataDir, 'genesis');
    if (fs.existsSync(srcGenesis)) {
      this.copyDirSync(srcGenesis, destGenesis);
    }
  }

  private copyDirSync(src: string, dest: string): void {
    if (!fs.existsSync(dest)) {
      fs.mkdirSync(dest, { recursive: true });
    }
    const entries = fs.readdirSync(src, { withFileTypes: true });
    for (const entry of entries) {
      const srcPath = path.join(src, entry.name);
      const destPath = path.join(dest, entry.name);
      if (entry.isDirectory()) {
        this.copyDirSync(srcPath, destPath);
      } else if (!fs.existsSync(destPath)) {
        fs.copyFileSync(srcPath, destPath);
      }
    }
  }

  private runInVenv(
    args: string[],
    onLine?: (line: string) => void,
  ): Promise<string> {
    const pythonBin = this.getVenvPython();
    return new Promise((resolve, reject) => {
      const proc = spawn(pythonBin, args, {
        cwd: this.nlsRoot,
        stdio: ['pipe', 'pipe', 'pipe'],
        env: { ...process.env, VIRTUAL_ENV: this.venvPath },
      });

      let stdout = '';
      let stderr = '';

      proc.stdout?.on('data', (data: Buffer) => {
        const text = data.toString();
        stdout += text;
        if (onLine) {
          text.split('\n').filter(Boolean).forEach(onLine);
        }
        this.broadcastLog('stdout', text);
      });

      proc.stderr?.on('data', (data: Buffer) => {
        const text = data.toString();
        stderr += text;
        if (onLine) {
          text.split('\n').filter(Boolean).forEach(onLine);
        }
        this.broadcastLog('stderr', text);
      });

      proc.on('close', (code) => {
        if (code === 0) {
          resolve(stdout);
        } else {
          reject(new Error(`Process exited with code ${code}: ${stderr}`));
        }
      });

      proc.on('error', reject);
    });
  }

  private async removeDirRobust(dirPath: string): Promise<void> {
    if (!fs.existsSync(dirPath)) return;

    // Attempt 1: rmSync with built-in retries (handles transient locks from
    // Windows Defender, Search Indexer, etc.)
    try {
      fs.rmSync(dirPath, {
        recursive: true,
        force: true,
        maxRetries: 5,
        retryDelay: 1_000,
      });
      return;
    } catch (err: any) {
      if (err.code !== 'EBUSY' && err.code !== 'EPERM' && err.code !== 'ENOTEMPTY') {
        throw err;
      }
      this.broadcastLog('stderr', `Directory locked (${err.code}), retrying...`);
    }

    // Attempt 2: rename out of the way first (usually succeeds even when
    // deletion is blocked), then delete the renamed copy.
    const tombstone = `${dirPath}-removing-${Date.now()}`;
    try {
      fs.renameSync(dirPath, tombstone);
      // Best-effort async cleanup of the renamed dir
      fs.rm(tombstone, { recursive: true, force: true, maxRetries: 3, retryDelay: 2_000 }, () => {});
      return;
    } catch {
      // rename also blocked -- fall through to final attempt
    }

    // Attempt 3: wait longer and try one last time
    await new Promise((r) => setTimeout(r, 5_000));
    try {
      fs.rmSync(dirPath, {
        recursive: true,
        force: true,
        maxRetries: 3,
        retryDelay: 2_000,
      });
    } catch (finalErr: any) {
      throw new Error(
        `Cannot remove ${dirPath} (${finalErr.code || finalErr.message}). ` +
        'Please close any programs that may be accessing this folder (including ' +
        'Explorer windows, antivirus scanners, or terminals) and try again.',
      );
    }
  }

  private execSimple(cmd: string, args: string[], timeout = 30_000): Promise<string> {
    return new Promise((resolve, reject) => {
      execFile(cmd, args, { timeout }, (error, stdout, stderr) => {
        if (error) {
          const detail = stderr?.trim() || stdout?.trim() || '';
          if (detail && !error.message.includes(detail)) {
            error.message += `\n${detail}`;
          }
          reject(error);
        } else {
          resolve((stdout || '').trim());
        }
      });
    });
  }

  /** Short UI-safe status from verbose pip/browser install log lines. */
  private summarizeInstallLine(line: string): string | null {
    const t = line.trim();
    if (!t) return null;

    const collecting = t.match(/^Collecting\s+(\S+)/);
    if (collecting) {
      const pkg = collecting[1].split(/[[\s]/)[0];
      return `Downloading ${pkg}…`;
    }

    if (t.startsWith('Installing collected packages')) {
      return 'Installing Python packages…';
    }

    const installed = t.match(/^Successfully installed\s+(.+)/);
    if (installed) {
      const count = installed[1].split(/\s+/).filter(Boolean).length;
      return count > 1 ? `Installed ${count} packages` : 'Package installed';
    }

    if (t.includes('Downloading') && t.includes('MB')) {
      const pkg = t.match(/Downloading\s+(\S+)/)?.[1]?.split(/[[\s]/)[0];
      return pkg ? `Downloading ${pkg}…` : 'Downloading packages…';
    }

    if (t.length > 96) {
      return `${t.slice(0, 93)}…`;
    }

    return t;
  }

  private updateStatus(
    stage: SetupStatus['stage'],
    message: string,
    progress: number,
    error?: string,
  ): void {
    this._status.stage = stage;
    this._status.message = message;
    this._status.progress = progress;
    if (error !== undefined) {
      this._status.error = error;
    }
    this.broadcastStatus();
  }

  private broadcastStatus(): void {
    for (const win of BrowserWindow.getAllWindows()) {
      win.webContents.send('setup:progress', this.status);
    }
  }

  private broadcastVisionPrefetch(message: string, progress: number): void {
    const payload = { message, progress };
    for (const win of BrowserWindow.getAllWindows()) {
      win.webContents.send('vision:prefetch-progress', payload);
    }
  }

  private broadcastLog(level: string, message: string): void {
    for (const win of BrowserWindow.getAllWindows()) {
      win.webContents.send('setup:log', { level, message });
    }
    try {
      this.logStream?.write(`[${level}] ${message}${message.endsWith('\n') ? '' : '\n'}`);
    } catch { /* best-effort */ }
  }

  private saveSetupState(): void {
    try {
      const reqHash = fs.existsSync(this.requirementsPath)
        ? this.hashFile(this.requirementsPath)
        : null;

      fs.writeFileSync(
        this.setupStatePath,
        JSON.stringify({
          complete: true,
          pythonPath: this.getVenvPython(),
          venvPath: this.venvPath,
          installedAt: new Date().toISOString(),
          requirementsHash: reqHash,
          llmfitVersion: getLlmfitBin() ? LLMFIT_VERSION : null,
        }),
        'utf-8',
      );
    } catch {
      // Non-critical
    }
  }

  private hashFile(filePath: string): string {
    const content = fs.readFileSync(filePath, 'utf-8');
    return crypto.createHash('sha256').update(content).digest('hex');
  }

  private loadRequirementsHash(): string | null {
    try {
      if (!fs.existsSync(this.setupStatePath)) return null;
      const data = JSON.parse(fs.readFileSync(this.setupStatePath, 'utf-8'));
      return data.requirementsHash ?? null;
    } catch {
      return null;
    }
  }

  private saveRequirementsHash(hash: string): void {
    try {
      let data: Record<string, any> = {};
      if (fs.existsSync(this.setupStatePath)) {
        data = JSON.parse(fs.readFileSync(this.setupStatePath, 'utf-8'));
      }
      data.requirementsHash = hash;
      fs.writeFileSync(this.setupStatePath, JSON.stringify(data), 'utf-8');
    } catch {
      // Non-critical
    }
  }
}
