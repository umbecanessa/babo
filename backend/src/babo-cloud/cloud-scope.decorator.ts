import { SetMetadata } from '@nestjs/common';

export const CLOUD_SCOPE_KEY = 'cloudScope';

/** Required API key scope(s) — user key must include at least one. */
export const CloudScope = (...scopes: string[]) =>
  SetMetadata(CLOUD_SCOPE_KEY, scopes);
