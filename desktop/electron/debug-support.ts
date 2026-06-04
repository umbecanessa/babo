/**
 * Desktop support / debug bundle helpers.
 *
 * Collects logs, agent state, and redacted config for user-facing export
 * (Settings → Support & Debug).
 */

import { app, shell } from 'electron';
import log from 'electron-log';
import archiver from 'archiver';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { createWriteStream } from 'fs';

import type { ConfigManager, NlsConfig } from './config-manager';
import type { RuntimeManager } from './runtime-manager';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type DebugArtifactKind =
  | 'runtime_log'
  | 'setup_log'
  | 'electron_log'
  | 'desktop_config'
  | 'agent_transcript'
  | 'agent_sessions'
  | 'agent_agentic_logs'
  | 'agent_state';

export interface DebugErrorEntry {
  id: string;
  source: 'runtime' | 'setup' | 'electron' | 'desktop';
  message: string;
  at: string | null;
}

export interface DebugArtifactInfo {
  kind: DebugArtifactKind;
  label: string;
  description: string;
  /** Present for per-agent exports */
  agentId?: string;
  agentName?: string;
  path: string;
  exists: boolean;
  sizeBytes: number;
}

export interface DebugSupportSummary {
  userDataPath: string;
  dataPath: string;
  appVersion: string;
  platform: string;
  errors: DebugErrorEntry[];
  artifacts: DebugArtifactInfo[];
}

export interface DebugExportResult {
  ok: boolean;
  path?: string;
  message: string;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const MAX_LOG_TAIL_BYTES = 2 * 1024 * 1024;
const MAX_FILE_BYTES = 8 * 1024 * 1024;
const MAX_AGENTIC_LOG_FILES = 12;
const ERROR_LINE_LIMIT = 80;

const RUNTIME_ERROR_RE =
  /\b(ERROR|CRITICAL)\b|Traceback \(most recent|Exception:|Fatal error/i;

// ---------------------------------------------------------------------------
// Paths
// ---------------------------------------------------------------------------

export function getDesktopPaths(config: ConfigManager): {
  userData: string;
  data: string;
} {
  return {
    userData: app.getPath('userData'),
    data: config.getDataDir(),
  };
}

function ensureExportParentDir(filePath: string): void {
  const dir = path.dirname(filePath);
  if (!dir || dir === '.' || dir === filePath) return;
  fs.mkdirSync(dir, { recursive: true });
}

function withZipExtension(filePath: string): string {
  return filePath.toLowerCase().endsWith('.zip') ? filePath : `${filePath}.zip`;
}

function electronLogDir(): string {
  try {
    return path.dirname(log.transports.file.getFile().path);
  } catch {
    return path.join(app.getPath('userData'), 'logs');
  }
}

function listElectronLogFiles(): string[] {
  const dir = electronLogDir();
  if (!fs.existsSync(dir)) return [];
  return fs
    .readdirSync(dir)
    .filter((n) => n.endsWith('.log'))
    .map((n) => path.join(dir, n))
    .sort((a, b) => {
      try {
        return fs.statSync(b).mtimeMs - fs.statSync(a).mtimeMs;
      } catch {
        return 0;
      }
    });
}

// ---------------------------------------------------------------------------
// Redaction
// ---------------------------------------------------------------------------

const SECRET_KEYS = new Set([
  'inferenceApiKey',
  'gpuWorkerSecret',
  'runtimeSharedSecret',
  'apiKey',
  'api_key',
  'secret',
  'token',
  'password',
]);

function redactValue(key: string, value: unknown): unknown {
  if (value == null) return value;
  const low = key.toLowerCase();
  if (
    SECRET_KEYS.has(key) ||
    low.endsWith('secret') ||
    low.endsWith('apikey') ||
    low.endsWith('_key') ||
    low.includes('api_key') ||
    low.includes('password') ||
    low === 'token' ||
    low.endsWith('_token')
  ) {
    if (typeof value === 'string' && value.length > 0) {
      return '[REDACTED]';
    }
  }
  if (Array.isArray(value)) {
    return value.map((v, i) => redactValue(`${key}[${i}]`, v));
  }
  if (typeof value === 'object') {
    return redactObject(value as Record<string, unknown>);
  }
  return value;
}

function redactObject(obj: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(obj)) {
    out[k] = redactValue(k, v);
  }
  return out;
}

export function redactDesktopConfig(cfg: NlsConfig): Record<string, unknown> {
  return redactObject(JSON.parse(JSON.stringify(cfg)) as Record<string, unknown>);
}

// ---------------------------------------------------------------------------
// Log parsing
// ---------------------------------------------------------------------------

function tailFileBytes(filePath: string, maxBytes: number): string {
  if (!fs.existsSync(filePath)) return '';
  const stat = fs.statSync(filePath);
  if (stat.size <= maxBytes) {
    return fs.readFileSync(filePath, 'utf-8');
  }
  const fd = fs.openSync(filePath, 'r');
  try {
    const buf = Buffer.alloc(maxBytes);
    fs.readSync(fd, buf, 0, maxBytes, stat.size - maxBytes);
    return buf.toString('utf-8');
  } finally {
    fs.closeSync(fd);
  }
}

function parseErrorsFromText(
  source: DebugErrorEntry['source'],
  text: string,
  prefix: string,
): DebugErrorEntry[] {
  const lines = text.split(/\r?\n/);
  const hits: DebugErrorEntry[] = [];
  let idx = 0;
  for (const line of lines) {
    if (!line.trim() || !RUNTIME_ERROR_RE.test(line)) continue;
    const trimmed = line.trim().slice(0, 500);
    hits.push({
      id: `${prefix}-${idx++}`,
      source,
      message: trimmed,
      at: extractTimestamp(line),
    });
    if (hits.length >= ERROR_LINE_LIMIT) break;
  }
  return hits;
}

function extractTimestamp(line: string): string | null {
  const iso = line.match(/\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}/);
  if (iso) return iso[0];
  const pipe = line.match(/^\[?(\d{2}:\d{2}:\d{2})/);
  return pipe ? pipe[1] : null;
}

export function collectRecentErrors(
  userData: string,
  runtimeLogTail?: string,
): DebugErrorEntry[] {
  const errors: DebugErrorEntry[] = [];

  const runtimePath = path.join(userData, 'runtime.log');
  const setupPath = path.join(userData, 'setup.log');

  if (runtimeLogTail) {
    errors.push(...parseErrorsFromText('runtime', runtimeLogTail, 'rt'));
  } else if (fs.existsSync(runtimePath)) {
    errors.push(
      ...parseErrorsFromText(
        'runtime',
        tailFileBytes(runtimePath, 512 * 1024),
        'rt',
      ),
    );
  }

  if (fs.existsSync(setupPath)) {
    errors.push(
      ...parseErrorsFromText(
        'setup',
        tailFileBytes(setupPath, 256 * 1024),
        'setup',
      ),
    );
  }

  for (const logFile of listElectronLogFiles().slice(0, 2)) {
    errors.push(
      ...parseErrorsFromText(
        'electron',
        tailFileBytes(logFile, 128 * 1024),
        path.basename(logFile, '.log'),
      ),
    );
  }

  // Newest first, dedupe by message
  const seen = new Set<string>();
  const deduped: DebugErrorEntry[] = [];
  for (const e of errors.reverse()) {
    const key = `${e.source}:${e.message}`;
    if (seen.has(key)) continue;
    seen.add(key);
    deduped.push(e);
    if (deduped.length >= 40) break;
  }
  return deduped;
}

// ---------------------------------------------------------------------------
// Artifacts
// ---------------------------------------------------------------------------

function fileInfo(
  kind: DebugArtifactKind,
  label: string,
  description: string,
  filePath: string,
  extra?: Partial<DebugArtifactInfo>,
): DebugArtifactInfo {
  let sizeBytes = 0;
  let exists = false;
  try {
    if (fs.existsSync(filePath)) {
      const st = fs.statSync(filePath);
      exists = st.isFile();
      sizeBytes = exists ? st.size : 0;
      if (st.isDirectory()) {
        exists = true;
        sizeBytes = dirSize(filePath, 3);
      }
    }
  } catch {
    exists = false;
  }
  return {
    kind,
    label,
    description,
    path: filePath,
    exists,
    sizeBytes,
    ...extra,
  };
}

function dirSize(dir: string, maxDepth: number): number {
  if (maxDepth < 0) return 0;
  let total = 0;
  try {
    for (const name of fs.readdirSync(dir)) {
      const p = path.join(dir, name);
      const st = fs.statSync(p);
      if (st.isFile()) total += st.size;
      else if (st.isDirectory()) total += dirSize(p, maxDepth - 1);
    }
  } catch {
    // ignore
  }
  return total;
}

function loadAgentName(agentDir: string): string | undefined {
  const metaPath = path.join(agentDir, 'agent_meta.json');
  if (!fs.existsSync(metaPath)) return undefined;
  try {
    const meta = JSON.parse(fs.readFileSync(metaPath, 'utf-8'));
    return meta.name || meta.display_name || undefined;
  } catch {
    return undefined;
  }
}

export function listDebugArtifacts(
  config: ConfigManager,
  liveRuntimeError?: string | null,
): DebugSupportSummary {
  const { userData, data } = getDesktopPaths(config);
  const artifacts: DebugArtifactInfo[] = [];

  artifacts.push(
    fileInfo(
      'runtime_log',
      'Runtime log',
      'Python agent runtime (uvicorn) — runtime.log',
      path.join(userData, 'runtime.log'),
    ),
  );
  artifacts.push(
    fileInfo(
      'setup_log',
      'Setup log',
      'Python environment setup and dependency sync',
      path.join(userData, 'setup.log'),
    ),
  );

  const electronLogs = listElectronLogFiles();
  if (electronLogs[0]) {
    artifacts.push(
      fileInfo(
        'electron_log',
        'Desktop app log',
        'Electron main process log',
        electronLogs[0],
      ),
    );
  }

  const configPath = path.join(userData, 'nls-config.json');
  artifacts.push({
    kind: 'desktop_config',
    label: 'Desktop config (redacted)',
    description: 'Current settings with secrets removed (generated on export)',
    path: configPath,
    exists: true,
    sizeBytes: fs.existsSync(configPath) ? fs.statSync(configPath).size : 0,
  });

  const agentsDir = path.join(data, 'agents');
  if (fs.existsSync(agentsDir)) {
    let agentIds: string[] = [];
    try {
      agentIds = fs.readdirSync(agentsDir);
    } catch {
      agentIds = [];
    }
    for (const agentId of agentIds) {
      const agentDir = path.join(agentsDir, agentId);
      let isDir = false;
      try {
        isDir = fs.statSync(agentDir).isDirectory();
      } catch {
        continue;
      }
      if (!isDir) continue;
      const name = loadAgentName(agentDir);
      const extra = { agentId, agentName: name };

      artifacts.push(
        fileInfo(
          'agent_transcript',
          name ? `${name} — chat transcript` : 'Chat transcript',
          'User-visible chat history (chat_transcript.jsonl)',
          path.join(agentDir, 'chat_transcript.jsonl'),
          extra,
        ),
      );
      artifacts.push(
        fileInfo(
          'agent_sessions',
          name ? `${name} — channel sessions` : 'Channel sessions',
          'Per-channel session indexes and histories',
          path.join(agentDir, 'sessions'),
          extra,
        ),
      );
      artifacts.push(
        fileInfo(
          'agent_agentic_logs',
          name ? `${name} — agentic loop logs` : 'Agentic loop logs',
          'Detailed agentic loop JSONL dumps',
          path.join(agentDir, 'agentic_logs'),
          extra,
        ),
      );
      artifacts.push(
        fileInfo(
          'agent_state',
          name ? `${name} — agent state` : 'Agent state',
          'Metadata, conversation history, config, plans',
          agentDir,
          extra,
        ),
      );
    }
  }

  const runtimeTail = fs.existsSync(path.join(userData, 'runtime.log'))
    ? tailFileBytes(path.join(userData, 'runtime.log'), 512 * 1024)
    : '';

  const errors = collectRecentErrors(userData, runtimeTail);
  if (liveRuntimeError?.trim()) {
    errors.unshift({
      id: 'runtime-live',
      source: 'runtime',
      message: liveRuntimeError.trim().slice(0, 500),
      at: new Date().toISOString(),
    });
  }

  return {
    userDataPath: userData,
    dataPath: data,
    appVersion: app.getVersion(),
    platform: `${process.platform} ${os.arch()}`,
    errors,
    artifacts,
  };
}

// ---------------------------------------------------------------------------
// Export helpers
// ---------------------------------------------------------------------------

async function copyFileToDir(
  src: string,
  destDir: string,
  destName?: string,
): Promise<boolean> {
  if (!fs.existsSync(src)) return false;
  const st = fs.statSync(src);
  const name = destName || path.basename(src);
  const dest = path.join(destDir, name);
  if (st.isDirectory()) {
    await copyDirLimited(src, dest);
    return true;
  }
  if (st.size > MAX_FILE_BYTES) {
    const tail = tailFileBytes(src, MAX_FILE_BYTES);
    fs.mkdirSync(destDir, { recursive: true });
    fs.writeFileSync(dest + '.tail.txt', tail, 'utf-8');
    return true;
  }
  fs.mkdirSync(destDir, { recursive: true });
  fs.copyFileSync(src, dest);
  return true;
}

async function copyDirLimited(
  srcDir: string,
  destDir: string,
  maxDepth = 4,
): Promise<void> {
  if (maxDepth < 0) return;
  fs.mkdirSync(destDir, { recursive: true });
  for (const name of fs.readdirSync(srcDir)) {
    if (name === 'workspace' || name === '__pycache__') continue;
    const src = path.join(srcDir, name);
    const dest = path.join(destDir, name);
    let st: fs.Stats;
    try {
      st = fs.statSync(src);
    } catch {
      continue;
    }
    if (st.isDirectory()) {
      await copyDirLimited(src, dest, maxDepth - 1);
    } else if (st.size <= MAX_FILE_BYTES) {
      fs.copyFileSync(src, dest);
    }
  }
}

async function copyAgentStateBundle(agentDir: string, destDir: string): Promise<void> {
  fs.mkdirSync(destDir, { recursive: true });
  const files = [
    'agent_meta.json',
    'conversation_history.json',
    'chat_transcript.jsonl',
    'job.json',
    'trust.json',
    'delegates.json',
    'guardrails_registry.jsonl',
    'wm_cryptex.json',
    'wm_common.json',
    'wm_personal.json',
  ];
  for (const f of files) {
    await copyFileToDir(path.join(agentDir, f), destDir);
  }
  for (const sub of ['config', 'sessions', 'plans', 'teams', 'memory']) {
    const p = path.join(agentDir, sub);
    if (fs.existsSync(p)) {
      await copyDirLimited(p, path.join(destDir, sub), 3);
    }
  }
  const agenticDir = path.join(agentDir, 'agentic_logs');
  if (fs.existsSync(agenticDir)) {
    const destAgentic = path.join(destDir, 'agentic_logs');
    fs.mkdirSync(destAgentic, { recursive: true });
    const logs = fs
      .readdirSync(agenticDir)
      .filter((n) => n.endsWith('.jsonl'))
      .map((n) => ({ name: n, path: path.join(agenticDir, n) }))
      .sort((a, b) => {
        try {
          return fs.statSync(b.path).mtimeMs - fs.statSync(a.path).mtimeMs;
        } catch {
          return 0;
        }
      })
      .slice(0, MAX_AGENTIC_LOG_FILES);
    for (const { name, path: lp } of logs) {
      await copyFileToDir(lp, destAgentic, name);
    }
  }
}

export async function exportDebugArtifact(
  config: ConfigManager,
  kind: DebugArtifactKind,
  destPath: string,
  agentId?: string,
): Promise<DebugExportResult> {
  const { userData, data } = getDesktopPaths(config);

  try {
    if (kind === 'desktop_config') {
      const redacted = redactDesktopConfig(config.get());
      ensureExportParentDir(destPath);
      fs.writeFileSync(destPath, JSON.stringify(redacted, null, 2), 'utf-8');
      return { ok: true, path: destPath, message: 'Config exported (secrets redacted)' };
    }

    if (kind === 'runtime_log') {
      const src = path.join(userData, 'runtime.log');
      if (!fs.existsSync(src)) {
        return { ok: false, message: 'Runtime log not found' };
      }
      await exportLogTail(src, destPath);
      return { ok: true, path: destPath, message: 'Runtime log exported' };
    }

    if (kind === 'setup_log') {
      const src = path.join(userData, 'setup.log');
      if (!fs.existsSync(src)) {
        return { ok: false, message: 'Setup log not found' };
      }
      await exportLogTail(src, destPath);
      return { ok: true, path: destPath, message: 'Setup log exported' };
    }

    if (kind === 'electron_log') {
      const logs = listElectronLogFiles();
      if (!logs[0]) {
        return { ok: false, message: 'Desktop log not found' };
      }
      await exportLogTail(logs[0], destPath);
      return { ok: true, path: destPath, message: 'Desktop log exported' };
    }

    if (!agentId) {
      return { ok: false, message: 'Agent id required' };
    }

    const agentDir = path.join(data, 'agents', agentId);
    if (!fs.existsSync(agentDir)) {
      return { ok: false, message: 'Agent data not found' };
    }

    if (kind === 'agent_transcript') {
      const src = path.join(agentDir, 'chat_transcript.jsonl');
      if (!fs.existsSync(src)) {
        return { ok: false, message: 'Chat transcript not found' };
      }
      ensureExportParentDir(destPath);
      fs.copyFileSync(src, destPath);
      return { ok: true, path: destPath, message: 'Chat transcript exported' };
    }

    if (kind === 'agent_sessions') {
      const src = path.join(agentDir, 'sessions');
      if (!fs.existsSync(src)) {
        return { ok: false, message: 'Sessions folder not found' };
      }
      const zipDest = withZipExtension(destPath);
      await zipPaths([{ src, archivePath: 'sessions' }], zipDest);
      return { ok: true, path: zipDest, message: 'Sessions exported' };
    }

    if (kind === 'agent_agentic_logs') {
      const src = path.join(agentDir, 'agentic_logs');
      if (!fs.existsSync(src)) {
        return { ok: false, message: 'Agentic logs not found' };
      }
      const zipDest = withZipExtension(destPath);
      await zipPaths([{ src, archivePath: 'agentic_logs' }], zipDest);
      return { ok: true, path: zipDest, message: 'Agentic logs exported' };
    }

    if (kind === 'agent_state') {
      const zipDest = withZipExtension(destPath);
      const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'babo-agent-export-'));
      try {
        await copyAgentStateBundle(agentDir, path.join(tmp, 'agent'));
        await zipPaths([{ src: path.join(tmp, 'agent'), archivePath: 'agent' }], zipDest);
      } finally {
        fs.rmSync(tmp, { recursive: true, force: true });
      }
      return { ok: true, path: zipDest, message: 'Agent state exported' };
    }

    return { ok: false, message: `Unknown export kind: ${kind}` };
  } catch (err: any) {
    return { ok: false, message: err?.message || 'Export failed' };
  }
}

async function exportLogTail(src: string, destPath: string): Promise<void> {
  const content = tailFileBytes(src, MAX_LOG_TAIL_BYTES);
  ensureExportParentDir(destPath);
  fs.writeFileSync(destPath, content, 'utf-8');
}

interface ZipEntry {
  src: string;
  archivePath: string;
}

async function zipDirectory(srcDir: string, destZip: string): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    ensureExportParentDir(destZip);
    const output = createWriteStream(destZip);
    const archive = archiver('zip', { zlib: { level: 6 } });

    output.on('close', () => resolve());
    output.on('error', reject);
    archive.on('error', reject);
    archive.pipe(output);
    archive.directory(srcDir, false);
    void archive.finalize();
  });
}

async function zipPaths(entries: ZipEntry[], destZip: string): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    ensureExportParentDir(destZip);
    const output = createWriteStream(destZip);
    const archive = archiver('zip', { zlib: { level: 6 } });

    output.on('close', () => resolve());
    output.on('error', reject);
    archive.on('error', reject);
    archive.pipe(output);

    for (const { src, archivePath } of entries) {
      if (!fs.existsSync(src)) continue;
      const st = fs.statSync(src);
      if (st.isDirectory()) {
        archive.directory(src, archivePath);
      } else {
        archive.file(src, { name: archivePath });
      }
    }

    void archive.finalize();
  });
}

export async function exportFullDebugBundle(
  config: ConfigManager,
  runtime: RuntimeManager,
  destZipPath: string,
): Promise<DebugExportResult> {
  const { userData, data } = getDesktopPaths(config);
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'babo-debug-'));

  try {
    const manifest = {
      exportedAt: new Date().toISOString(),
      appVersion: app.getVersion(),
      platform: process.platform,
      arch: os.arch(),
      hostname: os.hostname(),
      electronVersion: process.versions.electron,
      nodeVersion: process.versions.node,
      userDataPath: userData,
      dataPath: data,
      runtimeStatus: runtime.getStatus(),
      errors: collectRecentErrors(
        userData,
        fs.existsSync(path.join(userData, 'runtime.log'))
          ? tailFileBytes(path.join(userData, 'runtime.log'), 512 * 1024)
          : '',
      ),
    };
    fs.writeFileSync(
      path.join(tmp, 'manifest.json'),
      JSON.stringify(manifest, null, 2),
      'utf-8',
    );

    fs.writeFileSync(
      path.join(tmp, 'nls-config.redacted.json'),
      JSON.stringify(redactDesktopConfig(config.get()), null, 2),
      'utf-8',
    );

    const logsDir = path.join(tmp, 'logs');
    fs.mkdirSync(logsDir, { recursive: true });

    for (const [name, src] of [
      ['runtime.log', path.join(userData, 'runtime.log')],
      ['setup.log', path.join(userData, 'setup.log')],
      ['setup-state.json', path.join(userData, 'setup-state.json')],
    ] as const) {
      if (fs.existsSync(src)) {
        const st = fs.statSync(src);
        if (st.isFile() && st.size > MAX_LOG_TAIL_BYTES) {
          fs.writeFileSync(
            path.join(logsDir, name + '.tail.txt'),
            tailFileBytes(src, MAX_LOG_TAIL_BYTES),
            'utf-8',
          );
        } else {
          await copyFileToDir(src, logsDir);
        }
      }
    }

    for (const logFile of listElectronLogFiles().slice(0, 3)) {
      const base = path.basename(logFile);
      if (fs.statSync(logFile).size > MAX_LOG_TAIL_BYTES) {
        fs.writeFileSync(
          path.join(logsDir, base + '.tail.txt'),
          tailFileBytes(logFile, MAX_LOG_TAIL_BYTES),
          'utf-8',
        );
      } else {
        await copyFileToDir(logFile, logsDir, base);
      }
    }

    const agentsDir = path.join(data, 'agents');
    if (fs.existsSync(agentsDir)) {
      const agentsExport = path.join(tmp, 'agents');
      for (const agentId of fs.readdirSync(agentsDir)) {
        const agentDir = path.join(agentsDir, agentId);
        if (!fs.statSync(agentDir).isDirectory()) continue;
        await copyAgentStateBundle(agentDir, path.join(agentsExport, agentId));
      }
    }

    const squadsDir = path.join(data, 'squads');
    if (fs.existsSync(squadsDir)) {
      await copyDirLimited(squadsDir, path.join(tmp, 'squads'), 2);
    }

    await zipDirectory(tmp, destZipPath);

    return {
      ok: true,
      path: destZipPath,
      message: 'Full debug bundle exported',
    };
  } catch (err: any) {
    return { ok: false, message: err?.message || 'Export failed' };
  } finally {
    try {
      fs.rmSync(tmp, { recursive: true, force: true });
    } catch {
      // ignore cleanup errors
    }
  }
}

/** Reveal userData folder in the system file manager. */
export function revealUserDataFolder(): void {
  void shell.openPath(app.getPath('userData'));
}
