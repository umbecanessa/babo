export type CloudSubscriptionStatus =
  | 'none'
  | 'active'
  | 'past_due'
  | 'canceled'
  | 'lifetime_comp';

export interface CloudSubscriptionView {
  billingEnabled: boolean;
  status: CloudSubscriptionStatus;
  planId: string | null;
  billingExempt: boolean;
  hostedGx10Enabled: boolean;
  includedCreditCents: number;
  usedCreditCents: number;
  usedPercent: number;
  currentPeriodEnd: string | null;
  firstPaidAt: string | null;
  allowOverage: boolean;
  monthlySpendCapCents: number | null;
  onDemandEnabled: boolean;
  refundEligibleUntil: string | null;
  cloudMode?: boolean;
}

export interface CloudUsageRow {
  id: string;
  model: string;
  promptTokens: number;
  completionTokens: number;
  upstreamCostCents: number | null;
  createdAt: string;
}

export interface CloudUsageResponse {
  rows: CloudUsageRow[];
  subscription: CloudSubscriptionView;
}

export const CLOUD_BASIC_PRICE_AMOUNT = '$4.99';
export const CLOUD_BASIC_PRICE_LABEL = '$4.99/mo';

/** Format cents as USD for display. */
export function formatUsdCents(cents: number): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
  }).format(cents / 100);
}

export function includedRemainingPercent(view: CloudSubscriptionView): number {
  if (view.includedCreditCents <= 0) return 0;
  const remaining = Math.max(
    0,
    view.includedCreditCents - view.usedCreditCents,
  );
  return Math.round((remaining / view.includedCreditCents) * 100);
}

export function isPaidOrComp(view: CloudSubscriptionView): boolean {
  return (
    view.billingExempt ||
    view.status === 'lifetime_comp' ||
    view.status === 'active'
  );
}
