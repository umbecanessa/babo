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
): Promise<{ ok: boolean; status: number; body: string; latency: number }> {
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
    req.on('error', () => {
      resolve({ ok: false, status: 0, body: '', latency: Date.now() - start });
    });
    req.on('timeout', () => {
      req.destroy();
      resolve({ ok: false, status: 0, body: '', latency: Date.now() - start });
    });
  });
}

export async function probeLanHost(
  host: string,
  gpuWorkerSecret?: string,
): Promise<LanServiceProbe[]> {
  const base = host.replace(/^https?:\/\//, '').split('/')[0];
  const hostname = base.split(':')[0];
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
    let detail = r.ok ? `${r.latency}ms` : `unreachable (${r.status || 'error'})`;
    if (!r.ok && r.status === 401 && p.kind === 'vision') {
      detail = secret
        ? 'Unauthorized — check GPU secret'
        : 'Needs GPU worker secret (same as Babo Cloud / GX10)';
    }
    const entry: LanServiceProbe = {
      host: hostname,
      port: p.port,
      kind: p.kind,
      url:
        p.kind === 'inference'
          ? `http://${hostname}:${p.port}`
          : `http://${hostname}:${p.port}`,
      healthy: r.ok,
      detail,
    };

    if (r.ok && p.kind === 'inference') {
      try {
        const data = JSON.parse(r.body);
        const models = (data.data ?? []).map((m: { id?: string }) => m.id).filter(Boolean);
        entry.modelIds = models;
      } catch {
        entry.modelIds = [];
      }
    }

    if (r.ok && p.kind === 'transcribe') {
      try {
        const data = JSON.parse(r.body);
        entry.detail = data.model ? `model=${data.model}` : entry.detail;
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
  const url = `${base}/v1/models`;
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
