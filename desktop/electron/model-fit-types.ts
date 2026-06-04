/**
 * Model Fit — hardware-aware local/LAN model recommendations.
 * Uses llmfit when installed; falls back to a curated catalog.
 */

export type ModelFitLevel = 'perfect' | 'good' | 'marginal' | 'too_tight';

export type ModelFitTargetKind = 'local' | 'lan';

export interface GpuSnapshot {
  name: string;
  vramGb: number;
  vramFreeGb?: number;
  ramGb?: number;
  unifiedMemory?: boolean;
  backend?: 'cuda' | 'metal' | 'unknown';
  /** Set for LAN scans */
  host?: string;
}

export interface ModelFitRecommendation {
  displayName: string;
  /** Ollama tag (local) or HF / vLLM model id (LAN) */
  modelId: string;
  fitLevel: ModelFitLevel;
  estVramGb?: number;
  paramsB?: number;
  reason: string;
  /** Suggested runtime for this pick */
  runtime?: 'ollama' | 'vllm' | 'either';
}

export interface ModelFitResult {
  target: ModelFitTargetKind;
  host?: string;
  gpu: GpuSnapshot;
  /** At least one model at good/perfect for chat */
  localViable: boolean;
  recommendations: ModelFitRecommendation[];
  engine: 'llmfit' | 'heuristic';
  error?: string;
}

export interface LanSshOptions {
  user?: string;
  port?: number;
}
