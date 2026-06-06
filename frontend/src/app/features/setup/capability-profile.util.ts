import type { CapabilityProfile } from './capability-profile.model';

/** Healthy LAN vLLM/Ollama probe from the last capability scan. */
export function healthyLanInferenceProbe(profile: CapabilityProfile | null | undefined) {
  return profile?.scan?.lan?.find(
    (x) => x.kind === 'inference' && x.healthy && x.url?.trim(),
  );
}
