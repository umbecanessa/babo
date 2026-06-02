/** OpenRouter-style $/1M token rates (micro-cents per token for integer math). */

export interface ModelPricePerM {
  inputPerM: number;
  outputPerM: number;
}

const DEFAULT_PRICE: ModelPricePerM = {
  inputPerM: 0.3,
  outputPerM: 2.5,
};

const MODEL_PRICES: Record<string, ModelPricePerM> = {
  'google/gemini-2.5-flash': { inputPerM: 0.3, outputPerM: 2.5 },
  'google/gemini-2.0-flash': { inputPerM: 0.1, outputPerM: 0.4 },
  'openai/gpt-4o-mini': { inputPerM: 0.15, outputPerM: 0.6 },
  'openai/gpt-4o': { inputPerM: 2.5, outputPerM: 10.0 },
  'anthropic/claude-sonnet-4': { inputPerM: 3.0, outputPerM: 15.0 },
  'deepseek/deepseek-v3.2': { inputPerM: 0.14, outputPerM: 0.28 },
  'qwen/qwen3-coder': { inputPerM: 0.2, outputPerM: 0.8 },
};

export function getModelPrice(model: string): ModelPricePerM {
  const key = model.trim().toLowerCase();
  if (MODEL_PRICES[key]) return MODEL_PRICES[key];
  for (const [id, price] of Object.entries(MODEL_PRICES)) {
    if (key.includes(id.split('/').pop()!)) return price;
  }
  return DEFAULT_PRICE;
}

/** Whole cents (ceil) — conservative for pool debit. */
export function computeUpstreamCostCents(
  model: string,
  promptTokens: number,
  completionTokens: number,
): number {
  const { inputPerM, outputPerM } = getModelPrice(model);
  const dollars =
    (promptTokens / 1_000_000) * inputPerM +
    (completionTokens / 1_000_000) * outputPerM;
  if (dollars <= 0) return 0;
  return Math.max(1, Math.ceil(dollars * 100));
}
