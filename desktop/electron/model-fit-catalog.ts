/**
 * Curated models for heuristic fit when llmfit is not installed.
 */

import type { ModelFitLevel, ModelFitRecommendation } from './model-fit-types';

export interface CatalogEntry {
  displayName: string;
  modelId: string;
  paramsB: number;
  /** GB VRAM for a comfortable Q4-class run */
  minVramGb: number;
  runtime: 'ollama' | 'vllm' | 'either';
  useCase?: string;
}

export const MODEL_FIT_CATALOG: CatalogEntry[] = [
  {
    displayName: 'Phi-3 Mini',
    modelId: 'phi3:mini',
    paramsB: 3.8,
    minVramGb: 4,
    runtime: 'ollama',
    useCase: 'Lightweight chat',
  },
  {
    displayName: 'Llama 3.2 3B',
    modelId: 'llama3.2:3b',
    paramsB: 3,
    minVramGb: 4,
    runtime: 'ollama',
    useCase: 'Fast replies',
  },
  {
    displayName: 'Llama 3.2',
    modelId: 'llama3.2',
    paramsB: 3,
    minVramGb: 5,
    runtime: 'ollama',
    useCase: 'Everyday chat',
  },
  {
    displayName: 'Qwen 2.5 7B',
    modelId: 'qwen2.5:7b',
    paramsB: 7,
    minVramGb: 6,
    runtime: 'ollama',
    useCase: 'Strong general chat',
  },
  {
    displayName: 'Mistral 7B',
    modelId: 'mistral:7b',
    paramsB: 7,
    minVramGb: 6,
    runtime: 'ollama',
    useCase: 'Chat',
  },
  {
    displayName: 'Qwen 3 8B',
    modelId: 'qwen3:8b',
    paramsB: 8,
    minVramGb: 6,
    runtime: 'ollama',
    useCase: 'Balanced quality',
  },
  {
    displayName: 'DeepSeek R1 8B',
    modelId: 'deepseek-r1:8b',
    paramsB: 8,
    minVramGb: 6,
    runtime: 'ollama',
    useCase: 'Reasoning & coding',
  },
  {
    displayName: 'Qwen 2.5 14B',
    modelId: 'qwen2.5:14b',
    paramsB: 14,
    minVramGb: 10,
    runtime: 'ollama',
    useCase: 'Higher quality',
  },
  {
    displayName: 'Qwen 2.5 32B',
    modelId: 'qwen2.5:32b',
    paramsB: 32,
    minVramGb: 20,
    runtime: 'ollama',
    useCase: 'Large local model',
  },
  {
    displayName: 'Qwen 3.6 35B MoE (FP8)',
    modelId: 'Qwen/Qwen3.6-35B-A3B-FP8',
    paramsB: 35,
    minVramGb: 22,
    runtime: 'vllm',
    useCase: 'Homelab / LAN server',
  },
  {
    displayName: 'Qwen 3 32B',
    modelId: 'Qwen/Qwen3-32B',
    paramsB: 32,
    minVramGb: 20,
    runtime: 'vllm',
    useCase: 'LAN server',
  },
  {
    displayName: 'Llama 3.1 70B (quantized)',
    modelId: 'meta-llama/Llama-3.1-70B-Instruct',
    paramsB: 70,
    minVramGb: 40,
    runtime: 'vllm',
    useCase: 'Large LAN / workstation',
  },
];

function fitLevelForVram(vramGb: number, minVramGb: number): ModelFitLevel {
  if (vramGb >= minVramGb * 1.15) return 'perfect';
  if (vramGb >= minVramGb) return 'good';
  if (vramGb >= minVramGb * 0.72) return 'marginal';
  return 'too_tight';
}

function reasonForFit(level: ModelFitLevel, displayName: string, vramGb: number): string {
  switch (level) {
    case 'perfect':
      return `${displayName} fits your ${vramGb} GB GPU with headroom`;
    case 'good':
      return `${displayName} is a solid match for ${vramGb} GB VRAM`;
    case 'marginal':
      return `${displayName} may run slowly — tight on ${vramGb} GB VRAM`;
    default:
      return `${displayName} needs more VRAM than ${vramGb} GB`;
  }
}

export function recommendFromCatalog(
  gpu: { name: string; vramGb: number },
  options: { target: 'local' | 'lan'; limit?: number; preferOllama?: boolean },
): ModelFitRecommendation[] {
  const limit = options.limit ?? 6;
  const preferOllama = options.preferOllama ?? options.target === 'local';
  const vram = Math.max(0, gpu.vramGb);

  const scored = MODEL_FIT_CATALOG.map((entry) => {
    const fitLevel = fitLevelForVram(vram, entry.minVramGb);
    return {
      displayName: entry.displayName,
      modelId: entry.modelId,
      fitLevel,
      estVramGb: entry.minVramGb,
      paramsB: entry.paramsB,
      reason: reasonForFit(fitLevel, entry.displayName, vram),
      runtime: entry.runtime,
      _sort:
        fitLevel === 'perfect'
          ? 0
          : fitLevel === 'good'
            ? 1
            : fitLevel === 'marginal'
              ? 2
              : 9,
      _params: entry.paramsB,
      _ollama: entry.runtime === 'ollama' ? 0 : 1,
    };
  })
    .filter((r) => r.fitLevel !== 'too_tight')
    .filter((r) => (preferOllama ? r.runtime !== 'vllm' : true))
    .sort((a, b) => {
      if (a._sort !== b._sort) return a._sort - b._sort;
      if (a._ollama !== b._ollama) return a._ollama - b._ollama;
      return b._params - a._params;
    });

  return scored.slice(0, limit).map(({ _sort, _params, _ollama, ...rest }) => rest);
}

export function isLocalChatViable(recommendations: ModelFitRecommendation[]): boolean {
  return recommendations.some(
    (r) =>
      (r.fitLevel === 'perfect' || r.fitLevel === 'good') &&
      (r.paramsB ?? 0) >= 3,
  );
}
