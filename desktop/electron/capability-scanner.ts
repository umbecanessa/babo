/**
 * Device + LAN capability scanning for onboarding.
 */

import { execFile } from 'child_process';
import * as fs from 'fs';
import * as http from 'http';
import * as https from 'https';
import * as os from 'os';
import * as path from 'path';
import { promisify } from 'util';

import type {
  CapabilityScan,
  DeviceScan,
  InferenceCapabilities,
  LanServiceProbe,
  MultimodalConfidence,
} from './capability-types';

function resolveLanHostname(hostInput: string): string {
  let raw = hostInput.trim().replace(/^https?:\/\//, '').split('/')[0];
  if (raw.includes('@')) {
    raw = raw.slice(raw.indexOf('@') + 1);
  }
  return raw.split(':')[0];
}

const execFileAsync = promisify(execFile);

export interface ModelCapabilitiesFile {
  exact?: Record<string, { multimodal: boolean; notes?: string }>;
  patterns?: Array<{ match: string; multimodal: boolean }>;
  default?: { multimodal: string };
}

export function resolveMultimodal(
  modelId: string,
  registry: ModelCapabilitiesFile,
): InferenceCapabilities {
  const id = modelId.trim();
  const lower = id.toLowerCase();
  if (registry.exact?.[id]) {
    return {
      multimodal: registry.exact[id].multimodal ? 'true' : 'false',
      source: 'registry',
    };
  }
  for (const p of registry.patterns ?? []) {
    if (lower.includes(p.match.toLowerCase())) {
      return { multimodal: p.multimodal ? 'true' : 'false', source: 'registry' };
    }
  }
  return { multimodal: 'unknown', source: 'default' };
}

export function loadModelCapabilities(nlsRoot: string): ModelCapabilitiesFile {
  const p = path.join(nlsRoot, 'nls', 'config', 'model-capabilities.json');
  try {
    return JSON.parse(fs.readFileSync(p, 'utf-8')) as ModelCapabilitiesFile;
  } catch {
    return { default: { multimodal: 'unknown' } };
  }
}

function hardwareTier(device: Omit<DeviceScan, 'hardwareTier'>): string {
  if (device.vramGb >= 24 || device.ramGb >= 64) return 'A';
  if (device.vramGb >= 12) return 'B';
  if (device.vramGb >= 6) return 'C';
  if (device.hasMps) return 'D';
  if (!device.hasCuda && device.vramGb < 1) return 'E';
  return 'F';
}

export async function scanDevice(nlsRoot: string): Promise<DeviceScan> {
  const platform: DeviceScan['platform'] =
    os.platform() === 'win32'
      ? 'win32'
      : os.platform() === 'darwin'
        ? 'darwin'
        : 'linux';

  const ramGb = Math.round((os.totalmem() / 1024 ** 3) * 10) / 10;

  let vramGb = 0;
  let gpuName = 'None detected';
  let hasCuda = false;
  let hasMps = platform === 'darwin';

  if (platform === 'win32' || platform === 'linux') {
    try {
      const { stdout } = await execFileAsync(
        'nvidia-smi',
        ['--query-gpu=name,memory.total', '--format=csv,noheader,nounits'],
        { timeout: 8_000, windowsHide: true },
      );
      const line = stdout.trim().split('\n')[0];
      if (line) {
        const parts = line.split(',').map((s) => s.trim());
        gpuName = parts[0] || gpuName;
        const mib = parseFloat(parts[1] || '0');
        if (mib > 0) {
          vramGb = Math.round((mib / 1024) * 10) / 10;
          hasCuda = true;
        }
      }
    } catch {
      /* no NVIDIA */
    }
  }

  if (platform === 'darwin') {
    gpuName = 'Apple Silicon';
  }

  let hasMlxVlm = false;
  if (platform === 'darwin') {
    try {
      const py = path.join(nlsRoot, '..', 'desktop', 'python-env');
      // mlx only detectable after venv exists — best-effort skip
      hasMlxVlm = false;
    } catch {
      hasMlxVlm = false;
    }
  }

  const base = {
    platform,
    ramGb,
    vramGb,
    gpuName,
    hasCuda,
    hasMps,
    hasMlxVlm,
  };
  return { ...base, hardwareTier: hardwareTier(base) };
}

function fetchJson(
  url: string,
  timeoutMs = 5_000,
  headers?: Record<string, string>,
): Promise<{
  ok: boolean;
  status: number;
  body: string;
  latency: number;
  errorCode?: string;
}> {
  return new Promise((resolve) => {
    const start = Date.now();
    const lib = url.startsWith('https') ? https : http;
    const req = lib.get(url, { timeout: timeoutMs, headers }, (res) => {
      let body = '';
      res.on('data', (c) => { body += c; });
      res.on('end', () => {
        resolve({
          ok: res.statusCode !== undefined && res.statusCode >= 200 && res.statusCode < 300,
          status: res.statusCode ?? 0,
          body,
          latency: Date.now() - start,
        });
      });
    });
    req.on('error', (err: NodeJS.ErrnoException) => {
      resolve({
        ok: false,
        status: 0,
        body: '',
        latency: Date.now() - start,
        errorCode: err.code,
      });
    });
    req.on('timeout', () => {
      req.destroy();
      resolve({
        ok: false,
        status: 0,
        body: '',
        latency: Date.now() - start,
        errorCode: 'ETIMEDOUT',
      });
    });
  });
}

function probeFailureDetail(
  r: { status: number; errorCode?: string },
  kind: LanServiceProbe['kind'],
  secret: string,
): string {
  if (r.status === 401 && kind === 'vision') {
    return secret
      ? 'Unauthorized — check GPU worker secret'
      : 'Enter GPU worker secret above, then scan again';
  }
  if (r.status === 401) {
    return 'Unauthorized';
  }
  if (r.status > 0) {
    return `HTTP ${r.status}`;
  }
  if (r.errorCode === 'ECONNREFUSED') {
    return 'Not running on this port — start the container on the server';
  }
  if (r.errorCode === 'ETIMEDOUT' || r.errorCode === 'ESOCKETTIMEDOUT') {
    return 'Timed out — check firewall or host address';
  }
  if (r.errorCode === 'EHOSTUNREACH' || r.errorCode === 'ENETUNREACH') {
    return 'Host unreachable on your network';
  }
  return 'Unreachable — service may be stopped';
}

export async function probeLanHost(
  host: string,
  gpuWorkerSecret?: string,
): Promise<LanServiceProbe[]> {
  const hostname = resolveLanHostname(host);
  if (!hostname) return [];
  const secret = gpuWorkerSecret?.trim() ?? '';
  const probes: Array<{ port: number; kind: LanServiceProbe['kind']; path: string }> = [
    { port: 8000, kind: 'inference', path: '/v1/models' },
    { port: 8443, kind: 'vision', path: '/health' },
    { port: 4443, kind: 'transcribe', path: '/health' },
    { port: 11434, kind: 'inference', path: '/v1/models' },
  ];

  const results: LanServiceProbe[] = [];

  for (const p of probes) {
    const url = `http://${hostname}:${p.port}${p.path}`;
    const headers: Record<string, string> = {};
    if (secret && (p.kind === 'vision' || p.kind === 'transcribe')) {
      headers['X-GPU-Worker-Secret'] = secret;
    }
    const r = await fetchJson(url, 8_000, Object.keys(headers).length ? headers : undefined);
    let detail = r.ok ? `${r.latency}ms` : probeFailureDetail(r, p.kind, secret);
    const entry: LanServiceProbe = {
      host: hostname,
      port: p.port,
      kind: p.kind,
      url: `http://${hostname}:${p.port}`,
      healthy: r.ok,
      latencyMs: r.ok ? r.latency : undefined,
      detail,
    };

    if (r.ok && p.kind === 'inference') {
      entry.runtime = p.port === 11434 ? 'Ollama' : 'vLLM';
      try {
        const data = JSON.parse(r.body);
        const models = (data.data ?? []).map((m: { id?: string }) => m.id).filter(Boolean);
        entry.modelIds = models;
        if (models.length) {
          entry.primaryModel = models[0];
          entry.extraModelCount = Math.max(0, models.length - 1);
          detail =
            models.length > 1
              ? `${models[0]} +${models.length - 1} more`
              : models[0];
          entry.detail = detail;
        }
      } catch {
        entry.modelIds = [];
      }
    }

    if (r.ok && p.kind === 'vision') {
      try {
        const data = JSON.parse(r.body) as {
          model?: string;
          device?: string;
          loaded?: boolean;
        };
        if (data.model) {
          entry.primaryModel = data.model;
          entry.device = data.device;
          entry.modelLoaded = data.loaded;
          const loadNote = data.loaded ? 'ready' : 'cold start';
          entry.detail = `${data.model} · ${data.device ?? 'unknown'} · ${loadNote}`;
        }
      } catch { /* ignore */ }
    }

    if (r.ok && p.kind === 'transcribe') {
      try {
        const data = JSON.parse(r.body) as {
          model?: string;
          device?: string;
          compute_type?: string;
          loaded?: boolean;
        };
        if (data.model) {
          entry.primaryModel = data.model;
          entry.device = data.device ?? data.compute_type;
          entry.modelLoaded = data.loaded;
          const dev = entry.device ? ` · ${entry.device}` : '';
          entry.detail = `${data.model}${dev}`;
        }
      } catch { /* ignore */ }
    }

    results.push(entry);
  }

  const vllmUp = results.some(
    (r) => r.kind === 'inference' && r.port === 8000 && r.healthy,
  );
  if (vllmUp) {
    return results.filter((r) => !(r.kind === 'inference' && r.port === 11434));
  }

  return results;
}

export async function runCapabilityScan(
  nlsRoot: string,
  lanHost?: string,
): Promise<CapabilityScan> {
  const device = await scanDevice(nlsRoot);
  let lan: LanServiceProbe[] = [];
  if (lanHost?.trim()) {
    lan = await probeLanHost(lanHost.trim());
  }
  return {
    scannedAt: new Date().toISOString(),
    device,
    lan,
  };
}

/** Strip trailing /v1 for storage; Python client appends /v1 to paths. */
export function normalizeInferenceBaseUrl(url: string): string {
  return url.trim().replace(/\/+$/, '').replace(/\/v1$/i, '');
}

/** Nest Babo Cloud relay (`…/api/inference`) — use /v1/health, not /v1/models, for setup probes. */
export function isBaboCloudInferenceRelay(baseUrl: string): boolean {
  const base = normalizeInferenceBaseUrl(baseUrl).toLowerCase();
  return /\/api\/inference$/i.test(base) || /api\.babo\.agency\/api\/inference$/i.test(base);
}

function inferenceProbePath(baseUrl: string): string {
  if (isBaboCloudInferenceRelay(baseUrl)) {
    return '/v1/health';
  }
  return '/v1/models';
}

/** Ollama exposes tags at /api/tags (not OpenAI /v1/models). */
export async function testOllamaEndpoint(
  baseUrl: string,
): Promise<{ ok: boolean; message: string; latency: number; models: string[] }> {
  const base = normalizeInferenceBaseUrl(baseUrl);
  const url = `${base}/api/tags`;
  const r = await fetchJson(url, 12_000);
  if (!r.ok) {
    const message =
      r.status === 0
        ? 'Cannot reach Ollama — start it on this PC (ollama serve).'
        : r.body.slice(0, 200) || `HTTP ${r.status}`;
    return { ok: false, message, latency: r.latency, models: [] };
  }
  let models: string[] = [];
  try {
    const data = JSON.parse(r.body);
    models = (data.models ?? [])
      .map((m: { name?: string }) => m.name)
      .filter(Boolean);
  } catch { /* ignore */ }
  return {
    ok: true,
    message: models.length ? `Ollama — ${models.length} model(s)` : 'Ollama is running',
    latency: r.latency,
    models,
  };
}

export async function testInferenceEndpoint(
  baseUrl: string,
  apiKey?: string,
): Promise<{ ok: boolean; message: string; latency: number; models: string[] }> {
  const base = normalizeInferenceBaseUrl(baseUrl);
  if (/:11434(\/|$)/.test(base)) {
    return testOllamaEndpoint(base);
  }
  const baboRelay = isBaboCloudInferenceRelay(base);
  const url = `${base}${inferenceProbePath(base)}`;
  const headers: Record<string, string> = {};
  if (apiKey?.trim()) {
    headers.Authorization = `Bearer ${apiKey.trim()}`;
  }
  const r = await fetchJson(url, 12_000, Object.keys(headers).length ? headers : undefined);
  if (!r.ok) {
    const message =
      r.status === 0
        ? 'Cannot reach server — check the address and that the service is running.'
        : r.body.slice(0, 200) || `HTTP ${r.status}`;
    return {
      ok: false,
      message,
      latency: r.latency,
      models: [],
    };
  }
  if (baboRelay) {
    return {
      ok: true,
      message: 'Babo Cloud reachable',
      latency: r.latency,
      models: [],
    };
  }
  let models: string[] = [];
  try {
    const data = JSON.parse(r.body);
    models = (data.data ?? []).map((m: { id?: string }) => m.id).filter(Boolean);
  } catch { /* ignore */ }
  return {
    ok: true,
    message: models.length ? `Found ${models.length} model(s)` : 'Connected',
    latency: r.latency,
    models,
  };
}
