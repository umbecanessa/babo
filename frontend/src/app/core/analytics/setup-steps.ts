/** Setup wizard step index → stable funnel name. */
export const SETUP_STEP_NAMES: Record<number, string> = {
  0: 'welcome',
  1: 'prepare',
  2: 'device',
  3: 'thinking',
  4: 'extras',
  5: 'placement',
  6: 'signin',
  7: 'billing',
  8: 'ready',
  9: 'name',
};

export function setupStepName(step: number): string {
  return SETUP_STEP_NAMES[step] ?? `step_${step}`;
}
