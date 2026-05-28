/** Inference URL rules — must match desktop/electron + Python vllm_client (/v1 on requests). */

export interface CloudProvider {
  id: string;
  name: string;
  /** Stored without /v1; runtime adds /v1 for OpenAI-compatible calls */
  baseUrl: string;
  defaultModel: string;
}

/**
 * Local/dev only: auto-pick model from a LAN vLLM server (not shown in Babo Cloud wizard).
 * @see desktop/electron/capability-types.ts
 */
export const BABO_HOSTED_MODEL_ID = 'babo-hosted';

/** Default Babo Cloud chat model (OpenRouter; reliable agentic tool_calls). */
export const DEFAULT_BABO_CLOUD_MODEL = 'google/gemini-2.5-flash';

export interface BaboCloudModelOption {
  id: string;
  label: string;
}

/** Partner models available through Babo Cloud (api.babo.agency relay). */
export const BABO_CLOUD_MODELS: BaboCloudModelOption[] = [
  { id: DEFAULT_BABO_CLOUD_MODEL, label: 'Gemini 2.5 Flash' },
  { id: 'qwen/qwen3-coder', label: 'Qwen3 Coder' },
  { id: 'deepseek/deepseek-v3.2', label: 'DeepSeek V3.2' },
  { id: 'openai/gpt-4o-mini', label: 'GPT-4o mini' },
  { id: 'anthropic/claude-sonnet-4', label: 'Claude Sonnet' },
  { id: 'openai/gpt-4o', label: 'GPT-4o' },
  { id: 'qwen/qwen3.6-35b-a3b', label: 'Qwen 3.6 35B (legacy)' },
  { id: 'google/gemini-2.0-flash', label: 'Gemini 2.0 Flash' },
];

export function isBaboCloudModelId(model: string): boolean {
  return BABO_CLOUD_MODELS.some((m) => m.id === model);
}

/** Map legacy/scan defaults to a Babo Cloud chip id. */
export function resolveBaboCloudModelId(model?: string | null): string {
  const m = (model ?? '').trim();
  if (
    !m ||
    m === 'llama3.2' ||
    m === 'gpt-4o-mini' ||
    m === BABO_HOSTED_MODEL_ID ||
    m === 'qwen/qwen3.6-35b-a3b' ||
    /qwen3\.7/i.test(m)
  ) {
    return DEFAULT_BABO_CLOUD_MODEL;
  }
  if (isBaboCloudModelId(m)) return m;
  return DEFAULT_BABO_CLOUD_MODEL;
}

export const CLOUD_PROVIDERS: CloudProvider[] = [
  {
    id: 'openrouter',
    name: 'OpenRouter',
    baseUrl: 'https://openrouter.ai/api',
    defaultModel: 'openai/gpt-4o-mini',
  },
  {
    id: 'openai',
    name: 'OpenAI',
    baseUrl: 'https://api.openai.com',
    defaultModel: 'gpt-4o-mini',
  },
  {
    id: 'anthropic',
    name: 'Anthropic',
    baseUrl: 'https://api.anthropic.com',
    defaultModel: 'claude-sonnet-4-20250514',
  },
  {
    id: 'groq',
    name: 'Groq',
    baseUrl: 'https://api.groq.com/openai',
    defaultModel: 'llama-3.3-70b-versatile',
  },
  {
    id: 'together',
    name: 'Together',
    baseUrl: 'https://api.together.xyz',
    defaultModel: 'meta-llama/Llama-3.3-70B-Instruct-Turbo',
  },
];

export function stripInferenceV1Suffix(url: string): string {
  return url.trim().replace(/\/+$/, '').replace(/\/v1$/i, '');
}

export function inferenceUrlForTest(storedUrl: string): string {
  const base = stripInferenceV1Suffix(storedUrl);
  return `${base}/v1/models`;
}

export function matchCloudProvider(url: string): string {
  const lower = stripInferenceV1Suffix(url).toLowerCase();
  for (const p of CLOUD_PROVIDERS) {
    if (lower.includes(new URL(p.baseUrl).hostname)) {
      return p.id;
    }
  }
  return 'openrouter';
}
