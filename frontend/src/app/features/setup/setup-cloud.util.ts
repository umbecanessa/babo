import type { CapabilityProfile } from './capability-profile.model';
import { normalizeNestjsUrl } from './setup-backend.util';
import { DEFAULT_BABO_CLOUD_MODEL } from './setup-inference.util';

/** Nest API root with `/api` suffix. */
export function nestjsApiBase(nestjsUrl: string): string {
  const base = normalizeNestjsUrl(nestjsUrl);
  return base.endsWith('/api') ? base : `${base}/api`;
}

/** OpenAI-compatible inference base for Nest relay (`…/inference/v1`). */
export function baboInferenceRelayBase(nestjsUrl: string): string {
  return `${nestjsApiBase(nestjsUrl)}/inference`;
}

export function baboGpuRelayBase(nestjsUrl: string): string {
  return `${nestjsApiBase(nestjsUrl)}/gpu`;
}

/** Align profile placements with Babo Cloud relay after sign-in. */
export function applyBaboCloudPlacements(
  profile: CapabilityProfile,
  nestjsUrl: string,
): CapabilityProfile {
  const p = JSON.parse(JSON.stringify(profile)) as CapabilityProfile;
  const inf = p.inference;
  const apiBase = nestjsApiBase(nestjsUrl);
  const relayInf = `${apiBase}/inference`;

  if (inf.tier === 'hosted_babo' || inf.tier === 'byok_cloud') {
    inf.url = relayInf;
    if (inf.tier === 'hosted_babo' && !inf.model) {
      inf.model = DEFAULT_BABO_CLOUD_MODEL;
    }
  }

  const useGpuRelay =
    inf.tier === 'hosted_babo' ||
    inf.tier === 'byok_cloud';

  if (useGpuRelay) {
    if (p.visualCortex.tier === 'hosted_babo') {
      p.visualCortex.url = `${apiBase}/gpu`;
    }
    if (p.transcribe.tier === 'hosted_babo') {
      p.transcribe.url = `${apiBase}/gpu`;
    }
    if (p.embeddings.tier === 'hosted_babo') {
      p.embeddings.url = `${apiBase}/gpu`;
    }
  }

  return p;
}

export function usesBaboCloudRelay(profile: CapabilityProfile | null): boolean {
  if (!profile) return false;
  return (
    profile.inference.tier === 'hosted_babo' ||
    profile.inference.tier === 'byok_cloud'
  );
}
