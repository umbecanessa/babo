/**
 * Capability profile types for composable onboarding.
 *
 * Each workload (inference, visual_cortex, transcribe, embeddings) can be
 * placed independently: this machine, a LAN server, Babo-hosted, or BYOK cloud.
 *
 * See docs/architecture/capability-profiles-and-onboarding.md
 */

/** Where a workload runs. */
export type CapabilityTier =
  | 'self_local'
  | 'self_lan'
  | 'hosted_babo'
  | 'byok_cloud'
  | 'off';

/**
 * How desktop screen vision is provided.
 * See docs/architecture/capability-profiles-and-onboarding.md#vision-strategy-when-is-moondream-needed
 */
export type VisualCortexStrategy =
  | 'off'
  | 'on_demand_inference'
  | 'dedicated_vlm_local'
  | 'dedicated_vlm_lan'
  | 'ambient_via_inference';

/** Whether the configured chat model accepts images (unknown = assume text-only for safety). */
export type MultimodalConfidence = 'true' | 'false' | 'unknown';

export type CapabilitySource = 'registry' | 'user' | 'probe' | 'default';

export interface InferenceCapabilities {
  multimodal: MultimodalConfidence;
  source: CapabilitySource;
}

/** One model workload placement. */
export interface WorkloadPlacement {
  tier: CapabilityTier;
  /** Base URL (no trailing slash), e.g. http://192.168.68.96:8000/v1 */
  url?: string;
  /** Provider model id */
  model?: string;
  /** Optional secret for LAN GPU worker */
  secret?: string;
  /** Visual cortex only: auto | moondream | smolvlm | fastvlm */
  modelPreference?: string;
  /** Preferred VC strategy (if tier allows). */
  strategy?: VisualCortexStrategy;
  /** Human-readable reason from recommender */
  reason?: string;
}

/** Hardware scan of the machine running Babo desktop. */
export interface DeviceScan {
  platform: 'win32' | 'darwin' | 'linux';
  ramGb: number;
  vramGb: number;
  gpuName: string;
  hasCuda: boolean;
  hasMps: boolean;
  hasMlxVlm: boolean;
  /** A | B | C | D | E | F from docs */
  hardwareTier?: string;
}

/** A discovered service on the LAN. */
export interface LanServiceProbe {
  host: string;
  port: number;
  kind: 'inference' | 'transcribe' | 'vision' | 'embed' | 'unknown';
  url: string;
  modelIds?: string[];
  healthy: boolean;
  detail?: string;
}

/** Full scan output shown in onboarding. */
export interface CapabilityScan {
  scannedAt: string;
  device: DeviceScan;
  lan: LanServiceProbe[];
}

/** Optional sub-agent (delegate) chat model; uses `inference` when unset. */
export interface DelegateInferenceSettings {
  model?: string;
  /** When true, delegates always use `inference.model` */
  usePrimaryModel?: boolean;
}

/** User-facing capability profile persisted with desktop config. */
export interface CapabilityProfile {
  version: 1;
  profileId?: string;
  scan?: CapabilityScan;
  /** Detected or user-stated chat-model vision support */
  inferenceCapabilities?: InferenceCapabilities;
  inference: WorkloadPlacement;
  delegateInference?: DelegateInferenceSettings;
  visualCortex: WorkloadPlacement;
  transcribe: WorkloadPlacement;
  embeddings: WorkloadPlacement;
}

/** Defaults: OpenRouter-style cloud inference, local whisper, VC auto if GPU allows. */
export const DEFAULT_CAPABILITY_PROFILE: CapabilityProfile = {
  version: 1,
  inference: {
    tier: 'byok_cloud',
    url: 'https://openrouter.ai/api/v1',
    model: 'openai/gpt-4o-mini',
  },
  visualCortex: {
    tier: 'off',
    strategy: 'off',
  },
  transcribe: {
    tier: 'self_local',
  },
  embeddings: {
    tier: 'self_local',
    reason: 'Semantic code search on this computer',
  },
};

function normalizeBaseUrl(url: string): string {
  return url.replace(/\/v1\/?$/, '').replace(/\/$/, '');
}

const BABO_CLOUD_MODEL_IDS = new Set([
  'google/gemini-2.5-flash',
  'google/gemini-2.5-flash-lite',
  'qwen/qwen3-coder',
  'deepseek/deepseek-v3.2',
  'qwen/qwen3.6-35b-a3b',
  'openai/gpt-4o-mini',
  'anthropic/claude-sonnet-4',
  'openai/gpt-4o',
  'google/gemini-2.0-flash',
]);

/** Default Babo Cloud model — must pass OpenRouter tool-calling probe with tool_choice=auto. */
const DEFAULT_BABO_CLOUD_MODEL = 'google/gemini-2.5-flash';

/** GX10 private inference alias (desktop + Nest relay). */
export const BABO_HOSTED_MODEL_ID = 'babo-hosted';

/** Keep runtime/chat model ids valid for Babo Cloud (OpenRouter-style). */
export function resolveBaboCloudModelId(model?: string | null): string {
  const m = (model ?? '').trim();
  if (m === BABO_HOSTED_MODEL_ID) return BABO_HOSTED_MODEL_ID;
  if (
    !m ||
    m === 'llama3.2' ||
    m === 'gpt-4o-mini' ||
    m === 'qwen/qwen3.6-35b-a3b' ||
    /qwen3\.7/i.test(m)
  ) {
    return DEFAULT_BABO_CLOUD_MODEL;
  }
  if (BABO_CLOUD_MODEL_IDS.has(m)) {
    return m;
  }
  return DEFAULT_BABO_CLOUD_MODEL;
}

/** Normalize hosted_babo model + legacy inferenceModel fields. */
export function sanitizeCapabilityProfile(
  profile: CapabilityProfile,
): CapabilityProfile {
  if (profile.inference.tier !== 'hosted_babo') {
    return profile;
  }
  const model = resolveBaboCloudModelId(profile.inference.model);
  if (model === profile.inference.model) {
    return profile;
  }
  return {
    ...profile,
    inference: { ...profile.inference, model },
  };
}

/** Map profile → runtime env keys for the Python sidecar. */
export function capabilityProfileToRuntimeEnv(
  profile: CapabilityProfile,
  legacy: {
    inferenceApiKey?: string;
    runtimePort?: number;
    /** Nest API root, e.g. https://api.babo.agency/api */
    nestjsApiBase?: string;
  },
): Record<string, string> {
  const env: Record<string, string> = {};

  const inf = profile.inference;
  if (inf.tier === 'hosted_babo' && legacy.nestjsApiBase) {
    const apiBase = legacy.nestjsApiBase.replace(/\/+$/, '');
    env.NLS_VLLM_BASE_URL = `${apiBase}/inference/v1`;
    env.NLS_HF_MODEL = resolveBaboCloudModelId(inf.model);
  } else if (inf.tier === 'byok_cloud' && legacy.nestjsApiBase) {
    const apiBase = legacy.nestjsApiBase.replace(/\/+$/, '');
    env.NLS_VLLM_BASE_URL = `${apiBase}/inference/v1`;
    if (inf.model) env.NLS_HF_MODEL = inf.model;
  } else if (inf.tier !== 'off' && inf.url) {
    let base = inf.url.trim();
    if (inf.tier === 'byok_cloud' && !base.endsWith('/v1')) {
      base = `${base.replace(/\/+$/, '')}/v1`;
    }
    env.NLS_VLLM_BASE_URL = base;
    if (inf.model) env.NLS_HF_MODEL = inf.model;
  }
  if (legacy.inferenceApiKey) {
    env.NLS_INFERENCE_API_KEY = legacy.inferenceApiKey;
  }

  if (
    legacy.nestjsApiBase &&
    (inf.tier === 'self_local' || inf.tier === 'self_lan')
  ) {
    const apiBase = legacy.nestjsApiBase.replace(/\/+$/, '');
    env.NLS_BABO_CLOUD_INFERENCE_URL = `${apiBase}/inference/v1`;
  }

  const del = profile.delegateInference;
  const primaryModel = env.NLS_HF_MODEL;
  if (del && !del.usePrimaryModel && del.model?.trim()) {
    const dm =
      inf.tier === 'hosted_babo'
        ? resolveBaboCloudModelId(del.model)
        : del.model.trim();
    if (dm && dm !== primaryModel) {
      env.NLS_DELEGATE_HF_MODEL = dm;
    }
  }

  const vc = profile.visualCortex;
  const strategy =
    vc.strategy ??
    (vc.tier === 'off' ? 'off' : 'dedicated_vlm_local');
  const ambientOn =
    strategy !== 'off' &&
    vc.tier !== 'off' &&
    (strategy === 'dedicated_vlm_local' || strategy === 'dedicated_vlm_lan');
  env.NLS_VISUAL_CORTEX_ENABLED = ambientOn ? '1' : '0';
  env.NLS_VISUAL_CORTEX_STRATEGY = strategy;
  if (vc.modelPreference) {
    env.NLS_VISUAL_CORTEX_MODEL_PREFERENCE = vc.modelPreference;
  }

  const nestBase = legacy.nestjsApiBase;
  const visionUrl = workerUrl(profile.visualCortex, nestBase);
  const transcribeUrl = workerUrl(profile.transcribe, nestBase);
  const embedUrl = workerUrl(profile.embeddings, nestBase);

  if (visionUrl) {
    env.NLS_VISION_WORKER_URL = visionUrl;
  }
  if (transcribeUrl) {
    env.NLS_TRANSCRIBE_WORKER_URL = transcribeUrl;
  }
  if (embedUrl) {
    env.NLS_EMBED_WORKER_URL = embedUrl;
  }
  const fallback = visionUrl ?? transcribeUrl ?? embedUrl;
  if (fallback) {
    env.NLS_GPU_WORKER_URL = fallback;
  }

  const secret =
    profile.visualCortex.secret ||
    profile.transcribe.secret ||
    profile.embeddings.secret;
  if (secret) {
    env.NLS_GPU_WORKER_SECRET = secret;
    env.NLS_VISION_WORKER_SECRET = secret;
    env.NLS_TRANSCRIBE_WORKER_SECRET = secret;
  }

  const port = legacy.runtimePort ?? 9222;
  env.NLS_RUNTIME_PUBLIC_URL = `http://127.0.0.1:${port}`;

  return env;
}

function workerUrl(
  w: WorkloadPlacement,
  nestjsApiBase?: string,
): string | undefined {
  if (w.tier === 'hosted_babo' && nestjsApiBase) {
    const apiBase = nestjsApiBase.replace(/\/+$/, '');
    if (w.url?.includes('/vision')) return `${apiBase}/gpu`;
    if (w.url?.includes('/transcribe')) return `${apiBase}/gpu`;
    if (w.url?.includes('/embed')) return `${apiBase}/gpu`;
    return `${apiBase}/gpu`;
  }
  if (w.tier === 'self_lan' && w.url) {
    return normalizeBaseUrl(w.url);
  }
  return undefined;
}
