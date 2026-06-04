/**
 * GPU memory probing helpers — including GB10 / unified-memory fallbacks.
 */

import { execFile } from 'child_process';
import * as os from 'os';
import { promisify } from 'util';

import type { GpuSnapshot } from './model-fit-types';
import { execRemoteSshCommand } from './ssh-remote-exec';

const execFileAsync = promisify(execFile);

export function isUnifiedMemoryGpuName(name: string): boolean {
  return /gb10|grace blackwell|unified memory/i.test(name);
}

function parseNumericField(raw: string): number {
  const s = (raw ?? '').trim();
  if (!s || /^n\/a$/i.test(s) || /not supported/i.test(s)) {
    return NaN;
  }
  const n = parseFloat(s.replace(/[^0-9.]/g, ''));
  return Number.isFinite(n) ? n : NaN;
}

export function parseNvidiaSmiCsv(stdout: string, host?: string): GpuSnapshot | null {
  const lines = stdout.trim().split('\n').filter(Boolean);
  if (!lines.length) return null;

  let best: GpuSnapshot | null = null;
  for (const line of lines) {
    const parts = line.split(',').map((s) => s.trim());
    if (parts.length < 4) continue;
    const name = parts[1] || 'NVIDIA GPU';
    const freeMb = parseNumericField(parts[2] ?? '');
    const totalMb = parseNumericField(parts[3] ?? '');
    const totalGb =
      Number.isFinite(totalMb) && totalMb > 0
        ? Math.round((totalMb / 1024) * 10) / 10
        : 0;
    const freeGb =
      Number.isFinite(freeMb) && freeMb > 0
        ? Math.round((freeMb / 1024) * 10) / 10
        : undefined;
    const snap: GpuSnapshot = {
      name,
      vramGb: totalGb,
      vramFreeGb: freeGb,
      backend: 'cuda',
      host,
      unifiedMemory: isUnifiedMemoryGpuName(name),
    };
    if (!best || snap.vramGb > best.vramGb) best = snap;
  }
  return best;
}

export function systemRamGb(): number {
  return Math.round((os.totalmem() / 1024 ** 3) * 10) / 10;
}

export async function probeLocalUnifiedMemoryGb(): Promise<number> {
  return systemRamGb();
}

export async function probeRemoteMemTotalGb(options: {
  hostname: string;
  username: string;
  port?: number;
  password?: string;
}): Promise<number | null> {
  const { hostname, username, port = 22, password } = options;
  if (!hostname || !username.trim()) return null;

  const cmd = "grep -i MemTotal /proc/meminfo | awk '{print $2}'";

  try {
    let stdout: string;
    if (password?.trim()) {
      stdout = await execRemoteSshCommand({
        hostname,
        port,
        username: username.trim(),
        password: password.trim(),
        command: cmd,
        timeoutMs: 12_000,
      });
    } else {
      const target = `${username}@${hostname}`;
      const result = await execFileAsync(
        'ssh',
        [
          '-p',
          String(port),
          '-o',
          'BatchMode=yes',
          '-o',
          'ConnectTimeout=8',
          '-o',
          'StrictHostKeyChecking=accept-new',
          target,
          cmd,
        ],
        { timeout: 12_000, windowsHide: true },
      );
      stdout = result.stdout;
    }
    const kb = parseInt(stdout.trim().split('\n')[0] ?? '', 10);
    if (!Number.isFinite(kb) || kb <= 0) return null;
    return Math.round((kb / 1024 / 1024) * 10) / 10;
  } catch {
    return null;
  }
}

/** Fill missing VRAM on GB10 / unified-memory GPUs using system RAM. */
export async function applyUnifiedMemoryFallback(
  gpu: GpuSnapshot,
  options?: {
    remoteMem?: { hostname: string; username: string; port?: number; password?: string };
    localRamGb?: number;
  },
): Promise<GpuSnapshot> {
  const needsFallback =
    !gpu.vramGb ||
    !Number.isFinite(gpu.vramGb) ||
    gpu.unifiedMemory ||
    isUnifiedMemoryGpuName(gpu.name);

  if (!needsFallback) return gpu;

  let ramGb = options?.localRamGb ?? systemRamGb();
  if (options?.remoteMem) {
    const remote = await probeRemoteMemTotalGb(options.remoteMem);
    if (remote && remote > 0) ramGb = remote;
  }

  return {
    ...gpu,
    vramGb: ramGb,
    ramGb,
    unifiedMemory: true,
  };
}

export function formatGpuMemoryLabel(gpu: GpuSnapshot): string {
  if (gpu.unifiedMemory || isUnifiedMemoryGpuName(gpu.name)) {
    const gb = Number.isFinite(gpu.vramGb) && gpu.vramGb > 0 ? gpu.vramGb : '?';
    return `${gb} GB unified memory`;
  }
  const gb = Number.isFinite(gpu.vramGb) && gpu.vramGb > 0 ? gpu.vramGb : '?';
  return `${gb} GB VRAM`;
}
