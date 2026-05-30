/** Detect Babo Cloud billing / subscription errors (HTTP 402 or message hints). */
export function isBillingPaywallError(err: unknown): boolean {
  const status = (err as { status?: number })?.status;
  if (status === 402) return true;

  const message = extractErrorMessage(err).toLowerCase();
  return (
    message.includes('subscribe to babo cloud') ||
    message.includes('subscription required') ||
    message.includes('payment failed') ||
    message.includes('included usage exhausted') ||
    message.includes('spend cap reached')
  );
}

export function extractErrorMessage(err: unknown): string {
  if (!err || typeof err !== 'object') return '';
  const e = err as {
    error?: string | { message?: string };
    message?: string;
  };
  if (typeof e.error === 'string') return e.error;
  if (e.error && typeof e.error === 'object' && e.error.message) {
    return e.error.message;
  }
  return e.message ?? '';
}

export function billingPaywallHint(err: unknown): string {
  const msg = extractErrorMessage(err);
  if (msg) return msg;
  return 'A Babo Cloud subscription is required for hosted models.';
}
