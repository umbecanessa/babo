/**
 * Model Fit — scan GPU (local or SSH) and recommend models.
 */

import { execFile } from 'child_process';
import * as os from 'os';
import { promisify } from 'util';

import { getLlmfitBin } from './llmfit-installer';
import { scanDevice } from './capability-scanner';
import { execRemoteSshCommand } from './ssh-remote-exec';
import {
  applyUnifiedMemoryFallback,
  formatGpuMemoryLabel,
  parseNvidiaSmiCsv,
  systemRamGb,
} from './gpu-memory-probe';
import { isLocalChatViable, recommendFromCatalog } from './model-fit-catalog';
import type { ModelFitSnapshot } from './capability-types';
import type {
  GpuSnapshot,
  LanSshOptions,
  ModelFitRecommendation,
  ModelFitResult,
  ModelFitTargetKind,
} from './model-fit-types';

export function toModelFitSnapshot(result: ModelFitResult): ModelFitSnapshot {
  return {
    target: result.target,
    host: result.host,
    gpuName: result.gpu.name,
    vramGb: Number.isFinite(result.gpu.vramGb) ? result.gpu.vramGb : 0,
    unifiedMemory: result.gpu.unifiedMemory,
    memoryLabel: formatGpuMemoryLabel(result.gpu),
    localViable: result.localViable,
    engine: result.engine,
    recommendations: result.recommendations.map((r) => ({
      displayName: r.displayName,
      modelId: r.modelId,
      fitLevel: r.fitLevel,
      reason: r.reason,
      runtime: r.runtime,
    })),
    error: result.error,
  };
}

const execFileAsync = promisify(execFile);

const NVIDIA_SMI_ARGS = [
  '--query-gpu=index,name,memory.free,memory.total,memory.used',
  '--format=csv,noheader,nounits',
];

export function parseLanHostInput(
  hostInput: string,
  sshUser?: string,
): { hostname: string; sshUser?: string } {
  let raw = hostInput.trim().replace(/^https?:\/\//, '').split('/')[0];
  let user = sshUser?.trim() || undefined;
  if (raw.includes('@')) {
    const at = raw.indexOf('@');
    user = raw.slice(0, at) || user;
    raw = raw.slice(at + 1);
  }
  const hostname = raw.split(':')[0];
  return { hostname, sshUser: user };
}

async function probeNvidiaSmiLocal(): Promise<GpuSnapshot | null> {
  try {
    const { stdout } = await execFileAsync('nvidia-smi', NVIDIA_SMI_ARGS, {
      timeout: 12_000,
      windowsHide: true,
    });
    return parseNvidiaSmiCsv(stdout, undefined);
  } catch {
    return null;
  }
}

async function probeMetalUnified(): Promise<GpuSnapshot | null> {
  if (process.platform !== 'darwin') return null;
  try {
    const { stdout: memOut } = await execFileAsync('sysctl', ['-n', 'hw.memsize'], {
      timeout: 5_000,
    });
    const bytes = parseInt(memOut.trim(), 10);
    if (!Number.isFinite(bytes) || bytes <= 0) return null;
    const ramGb = Math.round((bytes / 1024 ** 3) * 10) / 10;
    let brand = 'Apple Silicon';
    try {
      const { stdout: brandOut } = await execFileAsync(
        'sysctl',
        ['-n', 'machdep.cpu.brand_string'],
        { timeout: 5_000 },
      );
      brand = brandOut.trim() || brand;
    } catch { /* ignore */ }
    return {
      name: brand,
      vramGb: ramGb,
      ramGb,
      unifiedMemory: true,
      backend: 'metal',
    };
  } catch {
    return null;
  }
}

export async function probeLocalGpu(nlsRoot: string): Promise<GpuSnapshot> {
  const nvidia = await probeNvidiaSmiLocal();
  if (nvidia) {
    nvidia.ramGb = systemRamGb();
    return await applyUnifiedMemoryFallback(nvidia, { localRamGb: nvidia.ramGb });
  }
  const metal = await probeMetalUnified();
  if (metal) return metal;

  const device = await scanDevice(nlsRoot);
  const gpu: GpuSnapshot = {
    name: device.gpuName,
    vramGb: device.vramGb,
    ramGb: device.ramGb,
    unifiedMemory: device.hasMps,
    backend: device.hasMps ? 'metal' : 'unknown',
  };
  return await applyUnifiedMemoryFallback(gpu, { localRamGb: device.ramGb });
}

export async function probeRemoteGpuViaSsh(
  hostInput: string,
  ssh?: LanSshOptions,
): Promise<{ ok: boolean; gpu?: GpuSnapshot; error?: string }> {
  const { hostname, sshUser } = parseLanHostInput(hostInput, ssh?.user);
  if (!hostname) {
    return { ok: false, error: 'Enter a host IP or hostname' };
  }
  if (!sshUser?.trim()) {
    return {
      ok: false,
      error: 'SSH username required to read GPU on a remote server (e.g. ubuntu@192.168.1.50)',
    };
  }

  const port = ssh?.port ?? 22;
  const remoteCmd = `nvidia-smi ${NVIDIA_SMI_ARGS.join(' ')}`;
  const password = ssh?.password?.trim();

  try {
    let stdout: string;
    if (password) {
      stdout = await execRemoteSshCommand({
        hostname,
        port,
        username: sshUser.trim(),
        password,
        command: remoteCmd,
        timeoutMs: 20_000,
      });
    } else {
      const target = `${sshUser}@${hostname}`;
      const args = [
        '-p',
        String(port),
        '-o',
        'BatchMode=yes',
        '-o',
        'ConnectTimeout=8',
        '-o',
        'StrictHostKeyChecking=accept-new',
        target,
        remoteCmd,
      ];
      const result = await execFileAsync('ssh', args, {
        timeout: 20_000,
        windowsHide: true,
      });
      stdout = result.stdout;
    }

    let gpu = parseNvidiaSmiCsv(stdout, hostname);
    if (!gpu) {
      return { ok: false, error: 'No NVIDIA GPU reported on that host' };
    }
    gpu = await applyUnifiedMemoryFallback(gpu, {
      remoteMem: {
        hostname,
        username: sshUser.trim(),
        port,
        password: password || undefined,
      },
    });
    return { ok: true, gpu };
  } catch (err: unknown) {
    const msg =
      (err as { message?: string })?.message ||
      (err as { stderr?: string })?.stderr ||
      'SSH failed';
    if (/not found|ENOENT/i.test(msg)) {
      return { ok: false, error: 'SSH client not found — install OpenSSH on this PC' };
    }
    if (/Permission denied|Authentication failed|All configured authentication methods failed/i.test(msg)) {
      return {
        ok: false,
        error: password
          ? 'SSH login failed — check username and password'
          : 'SSH authentication failed — enter your password below or add your key to the server',
      };
    }
    return { ok: false, error: msg.slice(0, 240) };
  }
}

function parseLlmfitRecommendations(stdout: string): ModelFitRecommendation[] | null {
  try {
    const data = JSON.parse(stdout) as {
      models?: Array<Record<string, unknown>>;
      recommendations?: Array<Record<string, unknown>>;
    };
    const rows = data.models ?? data.recommendations ?? [];
    if (!Array.isArray(rows) || !rows.length) return null;

    const mapFit = (raw: unknown): ModelFitRecommendation['fitLevel'] => {
      const s = String(raw ?? '').toLowerCase();
      if (s.includes('perfect')) return 'perfect';
      if (s.includes('good')) return 'good';
      if (s.includes('marginal')) return 'marginal';
      return 'good';
    };

    return rows.slice(0, 8).map((m) => {
      const name =
        String(m.name ?? m.model ?? m.id ?? m.display_name ?? 'Model').trim();
      const modelId = String(
        m.id ?? m.model_id ?? m.ollama_tag ?? m.name ?? name,
      ).trim();
      const fitLevel = mapFit(m.fit ?? m.fit_level ?? m.fitLevel);
      const vram =
        typeof m.est_vram_gb === 'number'
          ? m.est_vram_gb
          : typeof m.memory_gb === 'number'
            ? m.memory_gb
            : undefined;
      return {
        displayName: name,
        modelId,
        fitLevel,
        estVramGb: vram,
        paramsB: typeof m.params_b === 'number' ? m.params_b : undefined,
        reason: `llmfit: ${fitLevel} fit on your hardware`,
        runtime: modelId.includes('/') ? 'vllm' : 'ollama',
      };
    });
  } catch {
    return null;
  }
}

function resolveLlmfitExecutable(): string {
  return getLlmfitBin() ?? 'llmfit';
}

async function recommendWithLlmfit(gpu: GpuSnapshot): Promise<ModelFitRecommendation[] | null> {
  const llmfit = resolveLlmfitExecutable();
  const vramG = Math.max(1, Math.round(gpu.vramGb));
  const ramG = Math.max(4, Math.round(gpu.ramGb ?? os.totalmem() / 1024 ** 3));
  const memFlag = `${vramG}G`;
  const ramFlag = `${ramG}G`;
  try {
    const { stdout } = await execFileAsync(
      llmfit,
      ['--memory', memFlag, '--ram', ramFlag, 'recommend', '--json', '--limit', '8'],
      { timeout: 45_000, windowsHide: true, maxBuffer: 4 * 1024 * 1024 },
    );
    return parseLlmfitRecommendations(stdout);
  } catch {
    return null;
  }
}

export async function buildModelFitResult(
  target: ModelFitTargetKind,
  gpu: GpuSnapshot,
  options?: { host?: string; preferOllama?: boolean },
): Promise<ModelFitResult> {
  const preferOllama = options?.preferOllama ?? target === 'local';
  let recommendations = await recommendWithLlmfit(gpu);
  let engine: ModelFitResult['engine'] = 'llmfit';

  if (!recommendations?.length) {
    recommendations = recommendFromCatalog(gpu, { target, preferOllama });
    engine = 'heuristic';
  }

  const finalRecommendations = recommendations;
  const localViable = isLocalChatViable(finalRecommendations);

  return {
    target,
    host: options?.host ?? gpu.host,
    gpu,
    localViable,
    recommendations: finalRecommendations,
    engine,
  };
}

export async function modelFitLocal(nlsRoot: string): Promise<ModelFitResult> {
  const gpu = await probeLocalGpu(nlsRoot);
  return buildModelFitResult('local', gpu);
}

export async function modelFitRemote(
  hostInput: string,
  ssh?: LanSshOptions,
): Promise<ModelFitResult> {
  const { hostname } = parseLanHostInput(hostInput, ssh?.user);
  const remote = await probeRemoteGpuViaSsh(hostInput, ssh);
  if (!remote.ok || !remote.gpu) {
    const gpu: GpuSnapshot = {
      name: 'Unknown',
      vramGb: 0,
      host: hostname,
    };
    return {
      target: 'lan',
      host: hostname,
      gpu,
      localViable: false,
      recommendations: [],
      engine: 'heuristic',
      error: remote.error,
    };
  }
  remote.gpu.host = hostname;
  return buildModelFitResult('lan', remote.gpu, {
    host: hostname,
    preferOllama: false,
  });
}
