/**
 * Build recommended CapabilityProfile from scan results.
 */

import type {
  CapabilityProfile,
  CapabilityScan,
  InferenceCapabilities,
  WorkloadPlacement,
} from './capability-types';
import { DEFAULT_CAPABILITY_PROFILE } from './capability-types';
import { loadModelCapabilities, resolveMultimodal } from './capability-scanner';

export function recommendProfile(
  scan: CapabilityScan,
  nlsRoot: string,
  overrides?: Partial<CapabilityProfile>,
  options?: { gpuWorkerSecret?: string },
): CapabilityProfile {
  const registry = loadModelCapabilities(nlsRoot);
  const { device, lan } = scan;
  const gpuSecret = options?.gpuWorkerSecret?.trim() ?? '';

  const inferenceLan =
    lan.find((s) => s.kind === 'inference' && s.healthy && s.port === 8000) ??
    lan.find((s) => s.kind === 'inference' && s.healthy && s.port !== 11434) ??
    lan.find((s) => s.kind === 'inference' && s.healthy);
  const visionLan = lan.find((s) => s.kind === 'vision' && s.healthy);
  const transcribeLan = lan.find((s) => s.kind === 'transcribe' && s.healthy);

  const topFit = (kind: 'local' | 'lan') => {
    const snap = kind === 'local' ? scan.modelFit?.local : scan.modelFit?.lan;
    if (!snap?.recommendations?.length) return undefined;
    return (
      snap.recommendations.find(
        (r) => r.fitLevel === 'perfect' || r.fitLevel === 'good',
      ) ?? snap.recommendations[0]
    );
  };

  let inference: WorkloadPlacement;
  if (inferenceLan?.url) {
    const lanPick = topFit('lan');
    const running = inferenceLan.modelIds?.[0];
    inference = {
      tier: 'self_lan',
      url: inferenceLan.url,
      model: running ?? lanPick?.modelId ?? 'gpt-4o-mini',
      reason: running
        ? 'LAN inference server detected'
        : lanPick?.reason ?? 'LAN server — use My server in setup',
    };
  } else if (scan.modelFit?.lan?.localViable && scan.modelFit.lan.host) {
    const pick = topFit('lan');
    const host = scan.modelFit.lan.host.replace(/^https?:\/\//, '').split('/')[0].split(':')[0];
    inference = {
      tier: 'self_lan',
      url: `http://${host}:8000`,
      model: pick?.modelId ?? 'gpt-4o-mini',
      reason:
        pick?.reason ??
        'Your LAN GPU can run large models — add your vLLM server URL in setup',
    };
  } else if (scan.modelFit?.local?.localViable) {
    const pick = topFit('local');
    inference = {
      tier: 'self_local',
      url: 'http://127.0.0.1:11434',
      model: pick?.modelId ?? 'llama3.2:3b',
      reason: pick?.reason ?? 'Best match for this GPU — install Ollama and pull this model',
    };
  } else if (scan.modelFit?.local && !scan.modelFit.local.localViable) {
    inference = {
      tier: 'hosted_babo',
      model: 'google/gemini-2.5-flash',
      reason:
        'This PC is tight on VRAM for local chat — Babo Cloud is recommended',
    };
  } else if (device.vramGb >= 8) {
    inference = {
      tier: 'self_local',
      url: 'http://127.0.0.1:11434',
      model: 'llama3.2',
      reason: 'Local GPU — try Ollama on this computer',
    };
  } else {
    inference = {
      tier: 'hosted_babo',
      model: 'google/gemini-2.5-flash',
      reason: 'Babo Cloud recommended for this device',
    };
  }

  let inferenceCapabilities: InferenceCapabilities = { multimodal: 'unknown', source: 'default' };
  if (inference.model) {
    inferenceCapabilities = resolveMultimodal(inference.model, registry);
  }

  const multimodal = inferenceCapabilities.multimodal === 'true';
  const canLocalVlm = device.vramGb >= 6 && device.hasCuda;

  let visualCortex: WorkloadPlacement;
  if (multimodal) {
    visualCortex = {
      tier: 'off',
      strategy: 'off',
      reason: 'Chat model supports images — ambient vision off by default',
    };
  } else if (visionLan?.url) {
    visualCortex = {
      tier: 'self_lan',
      url: visionLan.url,
      strategy: 'dedicated_vlm_lan',
      secret: gpuSecret || undefined,
      reason: 'LAN vision worker detected',
    };
  } else if (canLocalVlm) {
    visualCortex = {
      tier: 'self_local',
      strategy: 'dedicated_vlm_local',
      modelPreference: 'auto',
      reason: 'This GPU can run Moondream for ambient vision',
    };
  } else {
    visualCortex = {
      tier: 'off',
      strategy: 'off',
      reason: 'Enable ambient vision after adding a GPU or LAN vision server',
    };
  }

  const transcribe: WorkloadPlacement = transcribeLan?.url
    ? {
        tier: 'self_lan',
        url: transcribeLan.url,
        reason: 'LAN voice server detected (optional — local mic is often faster)',
      }
    : {
        tier: 'self_local',
        reason: 'Voice recognition on this computer (recommended)',
      };

  const lanGpuFit = scan.modelFit?.lan?.localViable && scan.modelFit.lan.host;
  const profile: CapabilityProfile = {
    version: 1,
    profileId:
      inferenceLan || lanGpuFit
        ? 'lan-hub'
        : device.hardwareTier === 'B'
          ? 'enthusiast-desktop'
          : 'default',
    scan,
    inferenceCapabilities,
    inference,
    visualCortex,
    transcribe,
    embeddings: {
      tier: 'self_local',
      reason: 'Semantic code search on this computer',
    },
  };

  if (overrides) {
    return {
      ...profile,
      ...overrides,
      inference: { ...profile.inference, ...overrides.inference },
      visualCortex: { ...profile.visualCortex, ...overrides.visualCortex },
      transcribe: { ...profile.transcribe, ...overrides.transcribe },
      embeddings: { ...profile.embeddings, ...overrides.embeddings },
    };
  }

  return profile;
}

export function syncLegacyConfigFromProfile(
  profile: CapabilityProfile,
  apiKey: string,
): {
  inferenceUrl: string;
  inferenceModel: string;
  inferenceApiKey: string;
  gpuWorkerUrl?: string;
  gpuWorkerSecret?: string;
} {
  const inf = profile.inference;
  const out = {
    inferenceUrl: inf.url ?? DEFAULT_CAPABILITY_PROFILE.inference.url!,
    inferenceModel: inf.model ?? DEFAULT_CAPABILITY_PROFILE.inference.model!,
    inferenceApiKey: apiKey,
  };

  const visionUrl =
    profile.visualCortex.tier === 'self_lan' ? profile.visualCortex.url : undefined;
  const transcribeUrl =
    profile.transcribe.tier === 'self_lan' ? profile.transcribe.url : undefined;

  if (visionUrl || transcribeUrl) {
    return {
      ...out,
      gpuWorkerUrl: visionUrl ?? transcribeUrl,
      gpuWorkerSecret: profile.visualCortex.secret ?? profile.transcribe.secret,
    };
  }
  return out;
}
