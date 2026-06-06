import type { CapabilityTier } from '../../features/setup/capability-profile.model';
import {
  BABO_CLOUD_MODELS,
  BABO_HOSTED_MODEL_ID,
  CLOUD_PROVIDERS,
  baboCloudModelsForUser,
  resolveBaboCloudModelId,
} from '../../features/setup/setup-inference.util';
import type { ModelPickerOption } from './agent-model.service';

/** Babo Cloud catalog when the app is signed into / configured for the Nest backend. */
export function shouldOfferBaboCloudModels(opts: {
  tier: CapabilityTier;
  hasCloudApi: boolean;
}): boolean {
  if (!opts.hasCloudApi || opts.tier === 'off') return false;
  return true;
}

export function isHybridLocalInferenceTier(tier: CapabilityTier): boolean {
  return tier === 'self_local' || tier === 'self_lan';
}

/** Provider default ids when probing BYOK endpoints fails. */
export function providerDefaultModelOptions(providerId: string): ModelPickerOption[] {
  const prov = CLOUD_PROVIDERS.find((p) => p.id === providerId);
  if (!prov) return [];
  return [{ id: prov.defaultModel, label: labelForModelId(prov.defaultModel) }];
}

export function labelForModelId(modelId: string): string {
  const id = (modelId ?? '').trim();
  if (!id) return 'Default';
  if (id === BABO_HOSTED_MODEL_ID) return 'Babo Brain (GX10)';
  const cloud = BABO_CLOUD_MODELS.find((m) => m.id === id);
  if (cloud) return cloud.label;
  const slash = id.lastIndexOf('/');
  if (slash >= 0) {
    return id.slice(slash + 1).replace(/-/g, ' ');
  }
  return id;
}

/** Merge Babo Cloud catalog + discovered OpenAI-style model ids (deduped, stable order). */
export function mergeModelCatalog(opts: {
  tier: CapabilityTier;
  hasCloudApi: boolean;
  defaultModelId: string;
  discoveredIds?: string[];
  /** Extra ids from GET {api}/inference/v1/models (Babo Cloud relay). */
  cloudModelIds?: string[];
  includeProviderDefaults?: boolean;
  providerId?: string;
  hostedGx10Available?: boolean;
  hostedGx10Label?: string;
}): ModelPickerOption[] {
  const seen = new Set<string>();
  const out: ModelPickerOption[] = [];

  const add = (id: string, label?: string, source?: 'local' | 'cloud') => {
    const m = id.trim();
    if (!m || seen.has(m)) return;
    seen.add(m);
    out.push({ id: m, label: label ?? labelForModelId(m), source });
  };

  const def = opts.defaultModelId.trim();
  const resolvedDef =
    opts.tier === 'hosted_babo' ? resolveBaboCloudModelId(def) : def;
  const hybridLocal = isHybridLocalInferenceTier(opts.tier);
  const offerCloud = shouldOfferBaboCloudModels(opts);

  if (hybridLocal) {
    for (const id of opts.discoveredIds ?? []) {
      add(id, undefined, 'local');
    }
    if (resolvedDef) add(resolvedDef, undefined, 'local');
    if (opts.includeProviderDefaults && opts.providerId) {
      for (const o of providerDefaultModelOptions(opts.providerId)) {
        add(o.id, o.label, 'local');
      }
    }
    if (offerCloud) {
      for (const m of baboCloudModelsForUser(opts)) {
        add(m.id, m.label, 'cloud');
      }
      for (const id of opts.cloudModelIds ?? []) {
        add(id, undefined, 'cloud');
      }
    }
    return out;
  }

  if (offerCloud) {
    for (const m of baboCloudModelsForUser(opts)) {
      add(m.id, m.label, 'cloud');
    }
    for (const id of opts.cloudModelIds ?? []) {
      add(id, undefined, 'cloud');
    }
  }

  if (opts.includeProviderDefaults && opts.providerId) {
    for (const o of providerDefaultModelOptions(opts.providerId)) {
      add(o.id, o.label, 'local');
    }
  }

  for (const id of opts.discoveredIds ?? []) {
    add(id, undefined, 'local');
  }

  if (resolvedDef) {
    add(resolvedDef, undefined, isHybridLocalInferenceTier(opts.tier) ? 'local' : undefined);
  }

  return out;
}

/** Curated ids shown in the picker “Popular” section (order preserved). */
export const POPULAR_MODEL_IDS: readonly string[] = BABO_CLOUD_MODELS.map((m) => m.id);

export function filterModelPickerOptions(
  options: ModelPickerOption[],
  query: string,
): ModelPickerOption[] {
  const q = query.trim().toLowerCase();
  if (!q) return options;
  return options.filter(
    (o) =>
      o.label.toLowerCase().includes(q) || o.id.toLowerCase().includes(q),
  );
}

/** Split catalog into local setup, popular (default + curated), and the rest. */
export function partitionModelPickerOptions(
  options: ModelPickerOption[],
  defaultModelId: string,
  opts?: { tier?: CapabilityTier },
): {
  local: ModelPickerOption[];
  featured: ModelPickerOption[];
  more: ModelPickerOption[];
} {
  const byId = new Map(options.map((o) => [o.id, o]));
  const local: ModelPickerOption[] = [];
  const localIds = new Set<string>();

  const pushLocal = (id: string) => {
    if (localIds.has(id)) return;
    const o = byId.get(id);
    if (!o) return;
    localIds.add(id);
    local.push(o);
  };

  const hybridLocal = opts?.tier ? isHybridLocalInferenceTier(opts.tier) : false;
  if (hybridLocal) {
    for (const o of options) {
      if (o.source === 'local') {
        pushLocal(o.id);
      }
    }
  }

  const featured: ModelPickerOption[] = [];
  const featuredIds = new Set<string>(localIds);

  const pushFeatured = (id: string) => {
    if (featuredIds.has(id)) return;
    const o = byId.get(id);
    if (!o) return;
    featuredIds.add(id);
    featured.push(o);
  };

  const def = defaultModelId.trim();
  if (def && !hybridLocal) pushFeatured(def);
  pushFeatured(BABO_HOSTED_MODEL_ID);
  for (const id of POPULAR_MODEL_IDS) {
    pushFeatured(id);
  }

  const more = options
    .filter((o) => !featuredIds.has(o.id))
    .sort((a, b) => a.label.localeCompare(b.label, undefined, { sensitivity: 'base' }));

  return { local, featured, more };
}

/** Parse OpenAI GET /v1/models JSON. */
export function parseOpenAiModelList(payload: unknown): string[] {
  if (!payload || typeof payload !== 'object') return [];
  const data = (payload as { data?: unknown }).data;
  if (!Array.isArray(data)) return [];
  const ids: string[] = [];
  for (const row of data) {
    if (row && typeof row === 'object' && typeof (row as { id?: string }).id === 'string') {
      ids.push((row as { id: string }).id);
    }
  }
  return ids;
}
