import { ConfigService } from '@nestjs/config';

export function isValidHttpUrl(url: string): boolean {
  try {
    const parsed = new URL(url);
    return parsed.protocol === 'http:' || parsed.protocol === 'https:';
  } catch {
    return false;
  }
}

/** Public HTTPS page Stripe redirects to after Checkout / Portal (desktop uses file://). */
export function billingReturnEndpoint(config: ConfigService): string {
  const configured =
    config.get<string>('BILLING_RETURN_URL_BASE')?.trim() ||
    config.get<string>('APP_URL')?.trim() ||
    config.get<string>('PUBLIC_API_URL')?.trim() ||
    '';

  if (configured) {
    const base = configured.replace(/\/+$/, '');
    if (base.endsWith('/api/billing/return')) return base;
    if (base.endsWith('/api')) return `${base}/billing/return`;
    if (base.endsWith('/billing/return')) return base;
    return `${base}/api/billing/return`;
  }

  return 'https://api.babo.agency/api/billing/return';
}

export function inferBillingFlow(candidate?: string): 'setup' | 'settings' {
  const raw = (candidate || '').toLowerCase();
  if (raw.includes('/setup') || raw.includes('billing=success')) {
    return 'setup';
  }
  return 'settings';
}

export function normalizeBillingReturnUrl(
  config: ConfigService,
  candidate: string | undefined,
  flow?: 'setup' | 'settings',
): string {
  const trimmed = candidate?.trim();
  if (trimmed && isValidHttpUrl(trimmed)) {
    return trimmed;
  }

  const endpoint = billingReturnEndpoint(config);
  const resolvedFlow = flow ?? inferBillingFlow(trimmed);
  return `${endpoint}?status=success&flow=${resolvedFlow}`;
}
