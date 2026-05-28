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
  healthy: boolean;
  detail?: string;
}

export interface CapabilityScan {
  scannedAt: string;
  device: DeviceScan;
  lan: LanServiceProbe[];
}

export interface CapabilityProfile {
  version: 1;
  profileId?: string;
  scan?: CapabilityScan;
  inferenceCapabilities?: InferenceCapabilities;
  inference: WorkloadPlacement;
  visualCortex: WorkloadPlacement;
  transcribe: WorkloadPlacement;
  embeddings: WorkloadPlacement;
}
