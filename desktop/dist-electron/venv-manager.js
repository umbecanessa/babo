"use strict";
/**
 * NLS Desktop App -- Python Virtual Environment Manager
 *
 * Handles first-run setup: detects system Python, creates a venv,
 * and installs NLS dependencies.  Progress is streamed to the
 * renderer via IPC events.
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
exports.VenvManager = void 0;
const electron_1 = require("electron");
const child_process_1 = require("child_process");
const crypto = __importStar(require("crypto"));
const fs = __importStar(require("fs"));
const https = __importStar(require("https"));
const http = __importStar(require("http"));
const path = __importStar(require("path"));
const os = __importStar(require("os"));
// ---------------------------------------------------------------------------
// VenvManager
// ---------------------------------------------------------------------------
class VenvManager {
    venvPath;
    nlsRoot;
    requirementsPath;
    setupStatePath;
    setupLogPath;
    logStream = null;
    _status;
    constructor() {
        this.venvPath = path.join(electron_1.app.getPath('userData'), 'python-env');
        // NLS source root: two levels up from dist-electron/ in dev,
        // or from extraResources in production
        this.nlsRoot = electron_1.app.isPackaged
            ? path.join(process.resourcesPath, 'nls-source')
            : path.resolve(__dirname, '..', '..');
        this.requirementsPath = path.join(this.nlsRoot, 'requirements-desktop.txt');
        this.setupStatePath = path.join(electron_1.app.getPath('userData'), 'setup-state.json');
        this.setupLogPath = path.join(electron_1.app.getPath('userData'), 'setup.log');
        this._status = {
            stage: 'idle',
            message: '',
            progress: 0,
            pythonPath: null,
            venvPath: null,
            error: null,
        };
    }
    get status() {
        return { ...this._status };
    }
    /**
     * Check if the venv is already set up and ready.
     */
    isReady() {
        const pythonBin = this.getVenvPython();
        return fs.existsSync(pythonBin);
    }
    /**
     * Get the path to the Python binary inside the venv.
     */
    getVenvPython() {
        const isWin = os.platform() === 'win32';
        return isWin
            ? path.join(this.venvPath, 'Scripts', 'python.exe')
            : path.join(this.venvPath, 'bin', 'python');
    }
    /**
     * Get the NLS source root directory.
     */
    getNlsRoot() {
        return this.nlsRoot;
    }
    /**
     * Compare the current requirements-desktop.txt against the hash stored
     * at first install.  If it changed (e.g. after an app auto-update),
     * re-run pip install so the venv stays in sync.  No-ops if the venv
     * doesn't exist yet or the hash hasn't changed.
     */
    async checkDepsSync() {
        if (!this.isReady())
            return;
        if (!fs.existsSync(this.requirementsPath))
            return;
        const currentHash = this.hashFile(this.requirementsPath);
        const savedHash = this.loadRequirementsHash();
        if (currentHash !== savedHash) {
            this.updateStatus('installing', 'Updating dependencies after app update...', 10);
            try {
                await this.runInVenv(['-m', 'pip', 'install', '--verbose', '-r', this.requirementsPath], (line) => {
                    if (line.includes('Collecting') || line.includes('Installing')) {
                        const progress = Math.min(80, this._status.progress + 1);
                        this.updateStatus('installing', line.trim(), progress);
                    }
                });
                this.saveRequirementsHash(currentHash);
                // New pip packages may require post-install setup (e.g. playwright browsers)
                this.updateStatus('installing', 'Installing browser engine...', 82);
                await this.installBrowserEngine();
                // Pre-download Visual Cortex model if torch was newly added
                this.updateStatus('installing', 'Downloading visual model (Moondream)...', 88);
                await this.prefetchVisualModel();
            }
            catch (err) {
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
            }
            catch (err) {
                this.broadcastLog('stderr', `Node.js setup failed: ${err.message}`);
            }
        }
        this.updateStatus('ready', 'Dependencies up to date', 100);
    }
    /**
     * Run the full setup: detect Python, create venv, install deps.
     */
    async setup() {
        try {
            this.logStream = fs.createWriteStream(this.setupLogPath, { flags: 'a' });
            this.logStream.write(`\n${'='.repeat(60)}\n` +
                `Setup started at ${new Date().toISOString()}\n` +
                `Platform: ${os.platform()} ${os.arch()} | Node: ${process.version}\n` +
                `${'='.repeat(60)}\n`);
        }
        catch { /* best-effort */ }
        try {
            // Step 1: Detect system Python
            this.updateStatus('checking', 'Detecting Python installation...', 5);
            const systemPython = await this.detectPython();
            if (!systemPython) {
                throw new Error('Could not find or download Python 3.11+. ' +
                    'Please check your internet connection, or install Python manually from ' +
                    'https://python.org (ensure "Add Python to PATH" is checked during installation).');
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
            // Step 6: Pre-download Visual Cortex model (Moondream VLM)
            this.updateStatus('installing', 'Downloading visual model (Moondream)...', 85);
            await this.prefetchVisualModel();
            // Step 7: Upgrade to CUDA-enabled PyTorch if an NVIDIA GPU is present
            this.updateStatus('installing', 'Checking GPU for CUDA PyTorch...', 88);
            await this.ensureTorchCuda();
            // Step 8: Ensure standalone Node.js (for WhatsApp/Telegram bridges)
            this.updateStatus('installing', 'Setting up Node.js runtime...', 92);
            await this.ensureStandaloneNode();
            // Step 8: Ensure data directories
            this.updateStatus('installing', 'Setting up data directories...', 96);
            this.ensureDataDirs();
            // Done
            this._status.pythonPath = this.getVenvPython();
            this._status.venvPath = this.venvPath;
            this.updateStatus('ready', 'Setup complete', 100);
            this.saveSetupState();
        }
        catch (err) {
            this.updateStatus('error', err.message, 0, err.message);
            throw err;
        }
        finally {
            try {
                this.logStream?.end();
                this.logStream = null;
            }
            catch { /* best-effort */ }
        }
    }
    /**
     * Reset the venv (delete and re-create on next setup).
     */
    async reset() {
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
    async detectPython() {
        // Phase 1: Try standard commands on PATH
        const fromPath = await this.detectPythonFromPath();
        if (fromPath)
            return fromPath;
        // Phase 2: Scan well-known installation directories
        const fromKnown = await this.detectPythonFromKnownPaths();
        if (fromKnown)
            return fromKnown;
        // Phase 3: Download a standalone Python distribution
        return this.ensureStandalonePython();
    }
    async detectPythonFromPath() {
        const candidates = os.platform() === 'win32'
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
                if (!this.isPython311OrNewer(version))
                    continue;
                if (cmd === 'py') {
                    try {
                        const resolved = (await this.execSimple('py', ['-3', '-c', 'import sys; print(sys.executable)'])).trim();
                        if (resolved && fs.existsSync(resolved))
                            return resolved;
                    }
                    catch { /* fall through */ }
                    return 'py -3';
                }
                return cmd;
            }
            catch {
                // Try next candidate
            }
        }
        return null;
    }
    async detectPythonFromKnownPaths() {
        const pythonPaths = [];
        if (os.platform() === 'win32') {
            const localAppData = process.env['LOCALAPPDATA'] || '';
            const programFiles = process.env['ProgramFiles'] || 'C:\\Program Files';
            const systemRoot = process.env['SystemRoot'] || 'C:\\Windows';
            for (const minor of [14, 13, 12, 11]) {
                if (localAppData) {
                    pythonPaths.push(path.join(localAppData, 'Programs', 'Python', `Python3${minor}`, 'python.exe'));
                }
                pythonPaths.push(path.join(programFiles, `Python3${minor}`, 'python.exe'), `C:\\Python3${minor}\\python.exe`);
            }
            pythonPaths.push(path.join(systemRoot, 'py.exe'));
        }
        else if (os.platform() === 'darwin') {
            pythonPaths.push('/opt/homebrew/bin/python3', '/usr/local/bin/python3');
            for (const minor of [14, 13, 12, 11]) {
                pythonPaths.push(`/Library/Frameworks/Python.framework/Versions/3.${minor}/bin/python3`);
            }
        }
        else {
            pythonPaths.push('/usr/local/bin/python3', '/usr/bin/python3');
        }
        for (const p of pythonPaths) {
            if (!fs.existsSync(p))
                continue;
            try {
                if (path.basename(p) === 'py.exe') {
                    const version = await this.execSimple(p, ['-3', '--version']);
                    if (!this.isPython311OrNewer(version))
                        continue;
                    const resolved = (await this.execSimple(p, ['-3', '-c', 'import sys; print(sys.executable)'])).trim();
                    if (resolved && fs.existsSync(resolved))
                        return resolved;
                    continue;
                }
                const version = await this.execSimple(p, ['--version']);
                if (this.isPython311OrNewer(version))
                    return p;
            }
            catch {
                // Try next
            }
        }
        return null;
    }
    isPython311OrNewer(versionOutput) {
        const match = versionOutput.match(/Python (\d+)\.(\d+)/);
        if (!match)
            return false;
        const major = parseInt(match[1], 10);
        const minor = parseInt(match[2], 10);
        return major === 3 && minor >= 11;
    }
    // ─── Standalone Python (auto-download) ─────────────────────────
    static STANDALONE_PYTHON_VERSION = '3.12.12';
    static STANDALONE_PYTHON_TAG = '20260211';
    getStandalonePythonDir() {
        return path.join(electron_1.app.getPath('userData'), 'python-standalone');
    }
    getStandalonePythonBin() {
        const dir = this.getStandalonePythonDir();
        return os.platform() === 'win32'
            ? path.join(dir, 'python', 'python.exe')
            : path.join(dir, 'python', 'bin', 'python3');
    }
    getStandalonePythonUrl() {
        const ver = VenvManager.STANDALONE_PYTHON_VERSION;
        const tag = VenvManager.STANDALONE_PYTHON_TAG;
        const base = `https://github.com/astral-sh/python-build-standalone/releases/download/${tag}`;
        const triples = {
            'win32-x64': 'x86_64-pc-windows-msvc',
            'win32-arm64': 'aarch64-pc-windows-msvc',
            'darwin-x64': 'x86_64-apple-darwin',
            'darwin-arm64': 'aarch64-apple-darwin',
            'linux-x64': 'x86_64-unknown-linux-gnu',
            'linux-arm64': 'aarch64-unknown-linux-gnu',
        };
        const triple = triples[`${os.platform()}-${os.arch()}`];
        if (!triple)
            return null;
        return `${base}/cpython-${ver}+${tag}-${triple}-install_only_stripped.tar.gz`;
    }
    async ensureStandalonePython() {
        const pythonBin = this.getStandalonePythonBin();
        if (fs.existsSync(pythonBin)) {
            this.broadcastLog('stdout', `Using standalone Python at ${pythonBin}`);
            return pythonBin;
        }
        const url = this.getStandalonePythonUrl();
        if (!url) {
            this.broadcastLog('stderr', `No standalone Python build available for ${os.platform()}-${os.arch()}`);
            return null;
        }
        const standaloneDir = this.getStandalonePythonDir();
        fs.mkdirSync(standaloneDir, { recursive: true });
        const archivePath = path.join(standaloneDir, 'python.tar.gz');
        try {
            this.broadcastLog('stdout', `Downloading Python from ${url}...`);
            this.updateStatus('checking', 'Downloading Python runtime...', 6);
            await this.downloadFile(url, archivePath, (percent) => {
                this.updateStatus('checking', `Downloading Python runtime... ${percent}%`, 6 + Math.floor(percent * 0.07));
            });
            this.updateStatus('checking', 'Extracting Python runtime...', 13);
            this.broadcastLog('stdout', 'Extracting Python...');
            await this.execSimple('tar', ['xzf', archivePath, '-C', standaloneDir], 120_000);
            try {
                fs.unlinkSync(archivePath);
            }
            catch { /* non-critical */ }
            if (os.platform() !== 'win32' && fs.existsSync(pythonBin)) {
                fs.chmodSync(pythonBin, 0o755);
            }
            if (os.platform() === 'darwin') {
                try {
                    await this.execSimple('xattr', ['-dr', 'com.apple.quarantine', standaloneDir]);
                }
                catch { /* non-critical */ }
            }
            if (fs.existsSync(pythonBin)) {
                this.broadcastLog('stdout', `Standalone Python installed at ${pythonBin}`);
                return pythonBin;
            }
            this.broadcastLog('stderr', 'Python extraction succeeded but binary not found at expected path');
            return null;
        }
        catch (err) {
            this.broadcastLog('stderr', `Failed to download/extract Python: ${err.message}`);
            try {
                fs.unlinkSync(archivePath);
            }
            catch { /* clean up */ }
            try {
                fs.rmSync(standaloneDir, { recursive: true, force: true });
            }
            catch { /* clean up */ }
            return null;
        }
    }
    downloadFile(url, dest, onProgress) {
        return new Promise((resolve, reject) => {
            const doRequest = (currentUrl, redirectCount = 0) => {
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
                    response.on('data', (chunk) => {
                        downloaded += chunk.length;
                        if (totalSize > 0 && onProgress) {
                            onProgress(Math.round((downloaded / totalSize) * 100));
                        }
                    });
                    response.pipe(file);
                    file.on('finish', () => file.close(() => resolve()));
                    file.on('error', (err) => {
                        file.close();
                        try {
                            fs.unlinkSync(dest);
                        }
                        catch { /* ignore */ }
                        reject(err);
                    });
                })
                    .on('error', reject);
            };
            doRequest(url);
        });
    }
    async createVenv(systemPython) {
        // Remove existing venv if corrupted
        if (fs.existsSync(this.venvPath)) {
            const pythonBin = this.getVenvPython();
            if (!fs.existsSync(pythonBin)) {
                await this.removeDirRobust(this.venvPath);
            }
            else {
                return; // Venv already exists and looks valid
            }
        }
        const baseArgs = systemPython.startsWith('py ')
            ? ['-3', '-m', 'venv']
            : ['-m', 'venv'];
        const cmd = systemPython.startsWith('py ') ? 'py' : systemPython;
        try {
            await this.execSimple(cmd, [...baseArgs, this.venvPath]);
        }
        catch (firstErr) {
            this.broadcastLog('stderr', `venv creation failed: ${firstErr.message}`);
            this.broadcastLog('stdout', 'Retrying with --without-pip...');
            await this.removeDirRobust(this.venvPath);
            // Fallback: create venv without pip (ensurepip is often broken on
            // Windows Store Python and pre-release versions), then bootstrap pip.
            try {
                await this.execSimple(cmd, [...baseArgs, '--without-pip', this.venvPath]);
                await this.bootstrapPip();
            }
            catch (secondErr) {
                await this.removeDirRobust(this.venvPath);
                throw new Error(`Failed to create virtual environment.\n` +
                    `Attempt 1: ${firstErr.message}\n` +
                    `Attempt 2 (--without-pip): ${secondErr.message}`);
            }
        }
    }
    async bootstrapPip() {
        const pythonBin = this.getVenvPython();
        // Try ensurepip first (faster, no download)
        try {
            await this.execSimple(pythonBin, ['-m', 'ensurepip', '--upgrade']);
            return;
        }
        catch {
            this.broadcastLog('stdout', 'ensurepip unavailable, downloading get-pip.py...');
        }
        // Fall back to get-pip.py
        const getPipPath = path.join(this.venvPath, 'get-pip.py');
        await this.downloadFile('https://bootstrap.pypa.io/get-pip.py', getPipPath);
        try {
            await this.execSimple(pythonBin, [getPipPath], 120_000);
        }
        finally {
            try {
                fs.unlinkSync(getPipPath);
            }
            catch { /* non-critical */ }
        }
    }
    async installDeps() {
        if (!fs.existsSync(this.requirementsPath)) {
            throw new Error(`Requirements file not found: ${this.requirementsPath}`);
        }
        await this.runInVenv(['-m', 'pip', 'install', '--verbose', '-r', this.requirementsPath], (line) => {
            if (line.includes('Collecting') || line.includes('Installing') || line.includes('Successfully installed')) {
                const progress = Math.min(84, this._status.progress + 1);
                this.updateStatus('installing', line.trim(), progress);
            }
        });
    }
    async installBrowserEngine() {
        // browser-use wraps Playwright; its install command sets up Chromium
        // plus default extensions (uBlock Origin, ClearURLs, etc.).
        // Falls back to plain Playwright install if browser-use CLI isn't available.
        try {
            await this.runInVenv(['-m', 'browser_use', 'install'], (line) => {
                if (line.trim()) {
                    this.updateStatus('installing', line.trim(), 84);
                }
            });
        }
        catch {
            // Fallback: install Chromium via Playwright directly
            try {
                await this.runInVenv(['-m', 'playwright', 'install', 'chromium'], (line) => {
                    if (line.trim()) {
                        this.updateStatus('installing', line.trim(), 84);
                    }
                });
            }
            catch (err) {
                console.warn('Browser engine install failed:', err.message);
                this.broadcastLog('stderr', `Browser engine install failed: ${err.message}`);
            }
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
    async ensureTorchCuda() {
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
        }
        catch {
            // torch not installed yet — will be handled by deps install
            return;
        }
        // Probe for NVIDIA GPU via nvidia-smi
        let hasNvidiaGpu = false;
        try {
            const smi = await this.execSimple('nvidia-smi', ['--query-gpu=name', '--format=csv,noheader'], 8_000);
            hasNvidiaGpu = smi.trim().length > 0;
        }
        catch {
            // nvidia-smi not found → no NVIDIA GPU
        }
        if (!hasNvidiaGpu) {
            this.broadcastLog('stdout', 'No NVIDIA GPU detected — keeping CPU PyTorch');
            return;
        }
        this.broadcastLog('stdout', 'NVIDIA GPU found but torch is CPU-only — upgrading to CUDA PyTorch...');
        this.updateStatus('installing', 'Installing CUDA-enabled PyTorch (this may take a few minutes)...', 89);
        try {
            await this.runInVenv([
                '-m', 'pip', 'install', '--force-reinstall',
                'torch', 'torchvision',
                '--index-url', 'https://download.pytorch.org/whl/cu128',
            ], (line) => {
                if (line.includes('Downloading') || line.includes('Installing') || line.includes('Successfully')) {
                    this.updateStatus('installing', line.trim().slice(0, 80), 89);
                }
            });
            this.broadcastLog('stdout', 'CUDA PyTorch installed successfully');
            // Verify
            try {
                const check = await this.runInVenv([
                    '-c',
                    'import torch; v = torch.__version__; c = torch.cuda.is_available(); print(f"{v} cuda={c}")',
                ]);
                this.broadcastLog('stdout', `PyTorch after CUDA install: ${check.trim()}`);
            }
            catch {
                // non-critical verification failure
            }
        }
        catch (err) {
            this.broadcastLog('stderr', `CUDA PyTorch install failed (falling back to CPU torch): ${err.message}`);
        }
    }
    async prefetchVisualModel() {
        // Pre-download the Moondream VLM to the HuggingFace cache so the
        // Visual Cortex can load from disk at runtime without a network wait.
        // Non-fatal: if this fails the model will be downloaded on first use.
        try {
            await this.runInVenv(['-m', 'nls.scripts.prefetch_moondream'], (line) => {
                if (line.startsWith('PREFETCH:downloading:')) {
                    const model = line.replace('PREFETCH:downloading:', '');
                    this.updateStatus('installing', `Downloading visual model: ${model}...`, 86);
                }
                else if (line.startsWith('PREFETCH:done')) {
                    this.updateStatus('installing', 'Visual model cached', 91);
                }
                else if (line.startsWith('PREFETCH:error:')) {
                    this.broadcastLog('stderr', `Visual model prefetch: ${line.replace('PREFETCH:error:', '')}`);
                }
                else if (line.startsWith('PREFETCH:skip:')) {
                    this.broadcastLog('stdout', `Visual model prefetch skipped: ${line.replace('PREFETCH:skip:', '')}`);
                }
            });
        }
        catch (err) {
            console.warn('Visual model prefetch failed (non-fatal):', err.message);
            this.broadcastLog('stderr', `Visual model prefetch failed: ${err.message}`);
        }
    }
    // ─── Standalone Node.js (auto-download for bridges) ─────────────
    static STANDALONE_NODE_VERSION = '20.18.3';
    getStandaloneNodeDir() {
        return path.join(electron_1.app.getPath('userData'), 'node-standalone');
    }
    getNodeBin() {
        const dir = this.getStandaloneNodeDir();
        const bin = os.platform() === 'win32'
            ? path.join(dir, 'node', 'node.exe')
            : path.join(dir, 'node', 'bin', 'node');
        return fs.existsSync(bin) ? bin : null;
    }
    getNpmBin() {
        const dir = this.getStandaloneNodeDir();
        const bin = os.platform() === 'win32'
            ? path.join(dir, 'node', 'npm.cmd')
            : path.join(dir, 'node', 'bin', 'npm');
        return fs.existsSync(bin) ? bin : null;
    }
    getStandaloneNodeUrl() {
        const ver = VenvManager.STANDALONE_NODE_VERSION;
        const base = `https://nodejs.org/dist/v${ver}`;
        const platform = os.platform();
        const arch = os.arch();
        const targets = {
            'win32-x64': { file: `node-v${ver}-win-x64.zip`, ext: 'zip' },
            'win32-arm64': { file: `node-v${ver}-win-arm64.zip`, ext: 'zip' },
            'darwin-x64': { file: `node-v${ver}-darwin-x64.tar.gz`, ext: 'tar.gz' },
            'darwin-arm64': { file: `node-v${ver}-darwin-arm64.tar.gz`, ext: 'tar.gz' },
            'linux-x64': { file: `node-v${ver}-linux-x64.tar.gz`, ext: 'tar.gz' },
            'linux-arm64': { file: `node-v${ver}-linux-arm64.tar.gz`, ext: 'tar.gz' },
        };
        const target = targets[`${platform}-${arch}`];
        if (!target)
            return null;
        return { url: `${base}/${target.file}`, ext: target.ext };
    }
    async ensureStandaloneNode() {
        if (this.getNodeBin()) {
            this.broadcastLog('stdout', `Node.js already installed at ${this.getNodeBin()}`);
            return;
        }
        const target = this.getStandaloneNodeUrl();
        if (!target) {
            this.broadcastLog('stderr', `No Node.js build available for ${os.platform()}-${os.arch()} (bridges requiring Node will be disabled)`);
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
                this.updateStatus('installing', `Downloading Node.js runtime... ${percent}%`, 91 + Math.floor(percent * 0.03));
            });
            this.updateStatus('installing', 'Extracting Node.js runtime...', 94);
            this.broadcastLog('stdout', 'Extracting Node.js...');
            if (target.ext === 'zip') {
                await this.execSimple('tar', ['xf', archivePath, '-C', standaloneDir], 120_000);
            }
            else {
                await this.execSimple('tar', ['xzf', archivePath, '-C', standaloneDir], 120_000);
            }
            try {
                fs.unlinkSync(archivePath);
            }
            catch { /* non-critical */ }
            // Rename extracted directory (node-v20.x.x-os-arch) to just "node"
            const ver = VenvManager.STANDALONE_NODE_VERSION;
            const entries = fs.readdirSync(standaloneDir, { withFileTypes: true });
            const nodeDir = entries.find((e) => e.isDirectory() && e.name.startsWith(`node-v${ver}`));
            if (nodeDir) {
                const from = path.join(standaloneDir, nodeDir.name);
                const to = path.join(standaloneDir, 'node');
                fs.renameSync(from, to);
            }
            if (os.platform() === 'darwin') {
                try {
                    await this.execSimple('xattr', ['-dr', 'com.apple.quarantine', standaloneDir]);
                }
                catch { /* non-critical */ }
            }
            if (this.getNodeBin()) {
                this.broadcastLog('stdout', `Node.js installed at ${this.getNodeBin()}`);
            }
            else {
                this.broadcastLog('stderr', 'Node.js extraction succeeded but binary not found');
            }
        }
        catch (err) {
            this.broadcastLog('stderr', `Failed to install Node.js: ${err.message} (bridges will be disabled)`);
            try {
                fs.unlinkSync(archivePath);
            }
            catch { /* clean up */ }
        }
    }
    ensureDataDirs() {
        const dataDir = path.join(electron_1.app.getPath('userData'), 'data');
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
        // Copy genesis templates from NLS source if not present
        const srcGenesis = path.join(this.nlsRoot, 'data', 'genesis');
        const destGenesis = path.join(dataDir, 'genesis');
        if (fs.existsSync(srcGenesis)) {
            this.copyDirSync(srcGenesis, destGenesis);
        }
    }
    copyDirSync(src, dest) {
        if (!fs.existsSync(dest)) {
            fs.mkdirSync(dest, { recursive: true });
        }
        const entries = fs.readdirSync(src, { withFileTypes: true });
        for (const entry of entries) {
            const srcPath = path.join(src, entry.name);
            const destPath = path.join(dest, entry.name);
            if (entry.isDirectory()) {
                this.copyDirSync(srcPath, destPath);
            }
            else if (!fs.existsSync(destPath)) {
                fs.copyFileSync(srcPath, destPath);
            }
        }
    }
    runInVenv(args, onLine) {
        const pythonBin = this.getVenvPython();
        return new Promise((resolve, reject) => {
            const proc = (0, child_process_1.spawn)(pythonBin, args, {
                cwd: this.nlsRoot,
                stdio: ['pipe', 'pipe', 'pipe'],
                env: { ...process.env, VIRTUAL_ENV: this.venvPath },
            });
            let stdout = '';
            let stderr = '';
            proc.stdout?.on('data', (data) => {
                const text = data.toString();
                stdout += text;
                if (onLine) {
                    text.split('\n').filter(Boolean).forEach(onLine);
                }
                this.broadcastLog('stdout', text);
            });
            proc.stderr?.on('data', (data) => {
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
                }
                else {
                    reject(new Error(`Process exited with code ${code}: ${stderr}`));
                }
            });
            proc.on('error', reject);
        });
    }
    async removeDirRobust(dirPath) {
        if (!fs.existsSync(dirPath))
            return;
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
        }
        catch (err) {
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
            fs.rm(tombstone, { recursive: true, force: true, maxRetries: 3, retryDelay: 2_000 }, () => { });
            return;
        }
        catch {
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
        }
        catch (finalErr) {
            throw new Error(`Cannot remove ${dirPath} (${finalErr.code || finalErr.message}). ` +
                'Please close any programs that may be accessing this folder (including ' +
                'Explorer windows, antivirus scanners, or terminals) and try again.');
        }
    }
    execSimple(cmd, args, timeout = 30_000) {
        return new Promise((resolve, reject) => {
            (0, child_process_1.execFile)(cmd, args, { timeout }, (error, stdout, stderr) => {
                if (error) {
                    const detail = stderr?.trim() || stdout?.trim() || '';
                    if (detail && !error.message.includes(detail)) {
                        error.message += `\n${detail}`;
                    }
                    reject(error);
                }
                else {
                    resolve((stdout || '').trim());
                }
            });
        });
    }
    updateStatus(stage, message, progress, error) {
        this._status.stage = stage;
        this._status.message = message;
        this._status.progress = progress;
        if (error !== undefined) {
            this._status.error = error;
        }
        this.broadcastStatus();
    }
    broadcastStatus() {
        for (const win of electron_1.BrowserWindow.getAllWindows()) {
            win.webContents.send('setup:progress', this.status);
        }
    }
    broadcastLog(level, message) {
        for (const win of electron_1.BrowserWindow.getAllWindows()) {
            win.webContents.send('setup:log', { level, message });
        }
        try {
            this.logStream?.write(`[${level}] ${message}${message.endsWith('\n') ? '' : '\n'}`);
        }
        catch { /* best-effort */ }
    }
    saveSetupState() {
        try {
            const reqHash = fs.existsSync(this.requirementsPath)
                ? this.hashFile(this.requirementsPath)
                : null;
            fs.writeFileSync(this.setupStatePath, JSON.stringify({
                complete: true,
                pythonPath: this.getVenvPython(),
                venvPath: this.venvPath,
                installedAt: new Date().toISOString(),
                requirementsHash: reqHash,
            }), 'utf-8');
        }
        catch {
            // Non-critical
        }
    }
    hashFile(filePath) {
        const content = fs.readFileSync(filePath, 'utf-8');
        return crypto.createHash('sha256').update(content).digest('hex');
    }
    loadRequirementsHash() {
        try {
            if (!fs.existsSync(this.setupStatePath))
                return null;
            const data = JSON.parse(fs.readFileSync(this.setupStatePath, 'utf-8'));
            return data.requirementsHash ?? null;
        }
        catch {
            return null;
        }
    }
    saveRequirementsHash(hash) {
        try {
            let data = {};
            if (fs.existsSync(this.setupStatePath)) {
                data = JSON.parse(fs.readFileSync(this.setupStatePath, 'utf-8'));
            }
            data.requirementsHash = hash;
            fs.writeFileSync(this.setupStatePath, JSON.stringify(data), 'utf-8');
        }
        catch {
            // Non-critical
        }
    }
}
exports.VenvManager = VenvManager;
//# sourceMappingURL=venv-manager.js.map