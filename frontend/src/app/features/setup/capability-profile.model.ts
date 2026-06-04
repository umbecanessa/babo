/** Mirrors desktop/electron/capability-types.ts for the Angular wizard. */

export type CapabilityTier =
  | 'self_local'
  | 'self_lan'
  | 'hosted_babo'
  | 'byok_cloud'
  | 'off';

export type VisualCortexStrategy =
  | 'off'
  | 'on_demand_inference'
  | 'dedicated_vlm_local'
  | 'dedicated_vlm_lan'
  | 'ambient_via_inference';

export type MultimodalConfidence = 'true' | 'false' | 'unknown';

export interface InferenceCapabilities {
  multimodal: MultimodalConfidence;
  source: string;
}

export interface WorkloadPlacement {
  tier: CapabilityTier;
  url?: string;
  model?: string;
  secret?: string;
  modelPreference?: string;
  strategy?: VisualCortexStrategy;
  reason?: string;
}

export interface DeviceScan {
  platform: string;
  ramGb: number;
  vramGb: number;
  gpuName: string;
  hasCuda: boolean;
  hasMps: boolean;
  hasMlxVlm: boolean;
  hardwareTier?: string;
}

export interface LanServiceProbe {
  host: string;
  port: number;
  kind: string;
  url: string;
  modelIds?: string[];
  primaryModel?: string;
  extraModelCount?: number;
  latencyMs?: number;
  runtime?: string;
  device?: string;
  modelLoaded?: boolean;
  healthy: boolean;
  detail?: string;
}

export interface ModelFitRecommendationRow {
  displayName: string;
  modelId: string;
  fitLevel: string;
  reason: string;
  runtime?: string;
}

export interface ModelFitSnapshot {
  target: 'local' | 'lan';
  host?: string;
  gpuName: string;
  vramGb: number;
  unifiedMemory?: boolean;
  memoryLabel?: string;
  localViable: boolean;
  engine: 'llmfit' | 'heuristic';
  recommendations: ModelFitRecommendationRow[];
  error?: string;
}

export interface CapabilityScan {
  scannedAt: string;
  device: DeviceScan;
  lan: LanServiceProbe[];
  modelFit?: {
    local?: ModelFitSnapshot;
    lan?: ModelFitSnapshot;
  };
}

/** Optional sub-agent (delegate) chat model; uses `inference` when unset. */
export interface DelegateInferenceSettings {
  /** OpenRouter-style model id for delegate loops */
  model?: string;
  /** When true, delegates always use `inference.model` */
  usePrimaryModel?: boolean;
}

export interface CapabilityProfile {
  version: 1;
  profileId?: string;
  scan?: CapabilityScan;
  inferenceCapabilities?: InferenceCapabilities;
  inference: WorkloadPlacement;
  /** Sub-agents / delegates; defaults to primary inference model */
  delegateInference?: DelegateInferenceSettings;
  visualCortex: WorkloadPlacement;
  transcribe: WorkloadPlacement;
  embeddings: WorkloadPlacement;
}
