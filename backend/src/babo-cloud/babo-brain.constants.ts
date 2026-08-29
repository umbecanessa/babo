/** Wire id clients send for private GX10 inference. */
export const BABO_BRAIN_MODEL_ID = 'babo-hosted';

/** User-facing name — whatever GX10 serves is always this product. */
export const BABO_BRAIN_LABEL = 'Babo Brain';

export function isBaboBrainModelId(modelId: string | undefined | null): boolean {
  return (modelId ?? '').trim().toLowerCase() === BABO_BRAIN_MODEL_ID;
}
