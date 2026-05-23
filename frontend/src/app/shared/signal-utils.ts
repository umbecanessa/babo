/**
 * Shared NLS signal tag utilities.
 *
 * Used by both the message-list (chat pills) and signal-sidebar
 * (Neural State panel) for consistent formatting.
 */

/** A parsed NLS signal tag from response text. */
export interface SignalTag {
  /** Full raw tag text, e.g. "[LEARN:User.Name]" */
  raw: string;
  /** Signal type: LEARN, EVALUATE, RECALL, CONFLICT, etc. */
  type: string;
  /** Raw payload after the colon, e.g. "User.Name" */
  rawLabel: string;
  /** Human-readable label shown to the user. */
  label: string;
}

/** Signal-type -> color mapping for the tag pills/dots. */
export const TAG_COLORS: Record<string, string> = {
  // --- Base signals (from signals.json) ---
  LEARN:      '#34d399',  // emerald — hippocampal encoding
  EVALUATE:   '#a78bfa',  // violet — metacognitive evaluation
  RECALL:     '#38bdf8',  // sky — memory retrieval
  LOOKUP:     '#38bdf8',  // sky — semantic memory retrieval reflex
  REFLECT:    '#67e8f9',  // cyan — Default Mode Network
  CONNECT:    '#2dd4bf',  // teal — hippocampal relational binding
  DOUBT:      '#fb923c',  // orange — anterior insula conflict
  UNKNOWN:    '#9a9aaa',  // grey — ACC gap detection

  // --- Emotional / social signals ---
  ACC:        '#facc15',  // yellow — anterior cingulate cortex
  BONDING:    '#f472b6',  // pink — social bonding
  CLOSER:     '#fb923c',  // orange — relational closeness
  FEELING:    '#a78bfa',  // violet — affective state

  // --- ANS emotional sensing probes ---
  CURIOSITY:      '#fbbf24',  // amber — epistemic drive
  EVAL_POSITIVE:  '#34d399',  // emerald — success/correctness
  EVAL_NEGATIVE:  '#f87171',  // red — frustration/struggle
  EVAL_UNCERTAIN: '#fdba74',  // orange-light — confusion/doubt
  FOCUS:          '#818cf8',  // indigo — deep processing
  PLAN:           '#60a5fa',  // blue — planning/strategy

  // --- Brain-component signals ---
  INSULA:     '#14b8a6',  // teal — insular cortex comprehension
  AMYGDALA:   '#f472b6',  // pink — limbic affect
  PFC:        '#818cf8',  // indigo — prefrontal cortex judgment
  THALAMUS:   '#94a3b8',  // slate — routing gateway
  HYPOTHALAMUS:'#fb7185', // rose — hormonal regulation
  HIPPOCAMPUS:'#4ade80',  // green — episodic memory
  DMN:        '#c084fc',  // purple — default mode network

  // --- Higher-order signals ---
  CONFLICT:   '#f87171',  // red — conflict detection
  FORGET:     '#fbbf24',  // amber — forgetting
  DREAM:      '#c084fc',  // purple — dream context
  VALUES:     '#e879f9',  // fuchsia — values axioms
  TOOL:       '#60a5fa',  // blue — tool invocation
  COHERENCE:  '#a3e635',  // lime — reasoning coherence
};

export const DEFAULT_TAG_COLOR = '#9a9aaa';

/** Human-readable names for signal types. */
export const TYPE_LABELS: Record<string, string> = {
  LEARN:          'Learned',
  EVALUATE:       'Feeling',
  RECALL:         'Recalled',
  LOOKUP:         'Lookup',
  REFLECT:        'Reflection',
  CONNECT:        'Connected',
  DOUBT:          'Doubt',
  UNKNOWN:        'Unknown',
  ACC:            'Evaluating',
  BONDING:        'Bonding',
  CLOSER:         'Closeness',
  FEELING:        'Feeling',
  CURIOSITY:      'Curiosity',
  EVAL_POSITIVE:  'Positive',
  EVAL_NEGATIVE:  'Frustrated',
  EVAL_UNCERTAIN: 'Uncertain',
  FOCUS:          'Focused',
  PLAN:           'Planning',
  INSULA:     'Comprehension',
  AMYGDALA:   'Affect',
  PFC:        'Judgment',
  THALAMUS:   'Routing',
  HYPOTHALAMUS:'Hormones',
  HIPPOCAMPUS:'Memory',
  DMN:        'Reflection',
  CONFLICT:   'Conflict',
  FORGET:     'Forgot',
  DREAM:      'Dream',
  VALUES:     'Values',
  TOOL:       'Tool',
  COHERENCE:  'Coherence',
};

/** Regex to match signal tags anywhere in text.
 *  Handles both colon syntax [TYPE:payload] and dot syntax [TYPE.payload].
 *  Case-insensitive on the type name to catch model variations like [DOUBt:...]. */
export const TAG_REGEX = /\[([A-Za-z_]+)(?:[:.]([^\]]*))?\]/g;

/**
 * Convert a raw tag payload into a human-readable label.
 *
 * Examples:
 *   "ACC.Intrigued"                              -> "Intrigued"
 *   "User.Relationship.Bond"                     -> "Relationship Bond"
 *   "Agent.Personal.Dreams|I daydream about..."  -> "I daydream about..."
 *   "User.Name|Umberto"                          -> "Umberto"
 */
export function humanizeLabel(raw: string): string {
  // If there's a pipe, the part after is the actual fact/value
  const pipeIdx = raw.indexOf('|');
  if (pipeIdx !== -1) {
    return raw.substring(pipeIdx + 1).trim();
  }

  // Strip common prefixes: User., Agent., ACC., Personal.
  const parts = raw.split('.');
  const stripPrefixes = new Set(['User', 'Agent', 'ACC', 'Personal']);
  while (parts.length > 1 && stripPrefixes.has(parts[0])) {
    parts.shift();
  }

  // Join remaining with spaces and add spacing to camelCase
  return parts
    .join(' ')
    .replace(/([a-z])([A-Z])/g, '$1 $2');
}

/** Get the display color for a signal type (handles compound types like "EVALUATE:ACC.Curious"). */
export function tagColor(type: string): string {
  const base = type.split(':')[0].toUpperCase();
  return TAG_COLORS[base] || DEFAULT_TAG_COLOR;
}

/** Human-readable type label (handles compound types like "EVALUATE:ACC.Curious"). */
export function humanType(type: string): string {
  const base = type.split(':')[0].toUpperCase();
  return TYPE_LABELS[base] || base;
}

/**
 * Extract the domain portion from a signal object.
 *
 * Runtime sends EVALUATE signals with compound types like "EVALUATE:ACC.Curious"
 * where `domain` is empty.  This helper extracts the domain from the compound
 * type when `sig.domain` is not set.
 */
export function extractDomain(sig: { type: string; domain?: string }): string {
  if (sig.domain) return sig.domain;
  const parts = sig.type.split(':');
  return parts.length > 1 ? parts.slice(1).join(':') : '';
}

/** Parse signal tags from text, returning cleaned text and deduplicated tags. */
export function parseTags(content: string): { text: string; tags: SignalTag[] } {
  const tags: SignalTag[] = [];
  const seen = new Set<string>();
  let match: RegExpExecArray | null;
  const re = new RegExp(TAG_REGEX.source, 'g');
  while ((match = re.exec(content)) !== null) {
    const rawLabel = match[2] || '';
    const normalizedType = match[1].toUpperCase();
    const key = `${normalizedType}:${rawLabel}`;
    if (seen.has(key)) continue;
    seen.add(key);
    tags.push({
      raw: match[0],
      type: normalizedType,
      rawLabel,
      label: rawLabel ? humanizeLabel(rawLabel) : humanType(normalizedType),
    });
  }
  const text = content
    .replace(TAG_REGEX, '')
    .replace(/[^\S\n]{2,}/g, ' ')   // collapse multiple spaces/tabs but preserve newlines
    .replace(/\n{3,}/g, '\n\n')     // collapse 3+ consecutive newlines to 2
    .trim();
  return { text, tags };
}

/** Strip signal tags AND orphaned think tags from text. */
export function stripTags(content: string): string {
  return content
    .replace(TAG_REGEX, '')           // [SIGNAL:payload] tags
    .replace(/<\/?think>/g, '')       // orphaned <think> or </think>
    .replace(/[^\S\n]{2,}/g, ' ')    // collapse multiple spaces/tabs but preserve newlines
    .replace(/\n{3,}/g, '\n\n')      // collapse 3+ consecutive newlines to 2
    .trim();
}

// ═══════════════════════════════════════════════════════════════
// Reasoning / Thinking block utilities (<think>...</think>)
// ═══════════════════════════════════════════════════════════════

/** Result of parsing <think> blocks from model output. */
export interface ThinkingParsed {
  /** The model's internal reasoning (content inside <think> tags). */
  thinking: string;
  /** The visible response (everything outside <think> tags). */
  response: string;
}

/** Regex to match <think>...</think> blocks (greedy, multiline). */
const THINK_REGEX = /<think>([\s\S]*?)<\/think>/g;

/** Regex to strip orphaned opening/closing think tags. */
const ORPHAN_THINK_REGEX = /<\/?think>/g;

/**
 * Extract <think>...</think> blocks from model output.
 *
 * Qwen3-32B (and similar reasoning models) emit their internal
 * chain-of-thought inside `<think>` tags before the actual response.
 * This function separates the two.
 *
 * Also handles edge cases:
 *   - Orphaned `</think>` without matching `<think>` (server-side strip leak)
 *   - Orphaned `<think>` without matching `</think>`
 *   - Multiple think blocks
 *
 * @param content  Raw model output (may contain multiple <think> blocks).
 * @returns Object with `thinking` (concatenated reasoning) and `response` (cleaned output).
 */
export function parseThinking(content: string): ThinkingParsed {
  if (!content) return { thinking: '', response: '' };

  const thinkingParts: string[] = [];
  let match: RegExpExecArray | null;
  const re = new RegExp(THINK_REGEX.source, 'g');

  while ((match = re.exec(content)) !== null) {
    const inner = match[1].trim();
    if (inner) thinkingParts.push(inner);
  }

  let response = content
    .replace(THINK_REGEX, '')       // Strip matched <think>...</think> pairs
    .replace(ORPHAN_THINK_REGEX, '') // Strip any orphaned <think> or </think>
    .replace(/^\s+/, '')             // Trim leading whitespace
    .trim();

  return {
    thinking: thinkingParts.join('\n\n'),
    response,
  };
}

/**
 * Separate thinking from response in streaming text.
 *
 * During streaming the `</think>` tag may not have arrived yet.
 * This function handles partial state:
 *   - If `<think>` opened but not closed → all text after it is reasoning (in progress)
 *   - If `</think>` present → split cleanly
 *   - If no `<think>` at all → everything is response
 */
export function parseStreamingThinking(text: string): {
  thinking: string;
  response: string;
  isThinking: boolean;
} {
  if (!text) return { thinking: '', response: '', isThinking: false };

  // Check for completed think blocks first
  const hasClosedThink = text.includes('</think>');
  const hasOpenThink = text.includes('<think>');

  if (!hasOpenThink) {
    return { thinking: '', response: text, isThinking: false };
  }

  if (hasOpenThink && !hasClosedThink) {
    // Still thinking — everything after <think> is reasoning in progress
    const idx = text.indexOf('<think>');
    const before = text.substring(0, idx).replace(/<\/?think>/g, '').trim();
    const thinking = text.substring(idx + '<think>'.length).replace(/<\/?think>/g, '').trim();
    return { thinking, response: before, isThinking: true };
  }

  // Has completed think block(s) — parse normally
  const parsed = parseThinking(text);
  return { thinking: parsed.thinking, response: parsed.response, isThinking: false };
}
