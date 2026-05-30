import type { PlatformCapabilities } from '../models/platform-capabilities.model';

const DEFAULT_RETURN_BASE = 'https://api.babo.agency/api/billing/return';

export function isValidHttpUrl(url: string): boolean {
  try {
    const parsed = new URL(url);
    return parsed.protocol === 'http:' || parsed.protocol === 'https:';
  } catch {
    return false;
  }
}

export function billingReturnBaseFromCaps(
  caps: PlatformCapabilities | null | undefined,
): string {
  return caps?.billing?.returnUrlBase || DEFAULT_RETURN_BASE;
}

export function buildBillingReturnUrl(
  flow: 'setup' | 'settings',
  caps?: PlatformCapabilities | null,
): string {
  const base = billingReturnBaseFromCaps(caps);
  return `${base}?status=success&flow=${flow}`;
}

/** Prefer hosted HTTPS return URL when running in Electron (file:// renderer). */
export function resolveCheckoutReturnUrl(
  flow: 'setup' | 'settings',
  caps?: PlatformCapabilities | null,
): string {
  if (typeof window === 'undefined') {
    return buildBillingReturnUrl(flow, caps);
  }

  const origin = window.location.origin;
  if (isValidHttpUrl(`${origin}/`)) {
    if (flow === 'setup') {
      return `${origin}/setup?billing=success`;
    }
    return `${origin}${window.location.pathname}?section=billing&billing=success`;
  }

  return buildBillingReturnUrl(flow, caps);
}

export function openExternalUrl(url: string): void {
  const nls = (window as any).nls;
  if (nls?.openExternal) {
    nls.openExternal(url);
    return;
  }
  window.open(url, '_blank', 'noopener,noreferrer');
}
