/** Must match OSS `CLOUD_BILLING_PROVIDER` string token. */
export const CLOUD_BILLING_PROVIDER = 'CLOUD_BILLING_PROVIDER';

export type CloudSubscriptionStatus =
  | 'none'
  | 'active'
  | 'past_due'
  | 'canceled'
  | 'lifetime_comp';

export interface SubscriptionView {
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
}

export interface CloudBillingProvider {
  isEnabled(): boolean;
  assertCloudAccess(userId: string): Promise<void>;
  onUserRegistered(userId: string): Promise<void>;
  recordUsage(
    userId: string,
    upstreamCostCents: number,
    opts?: { placement?: string },
  ): Promise<void>;
  getSubscriptionView(userId: string): Promise<SubscriptionView>;
  createCheckoutSession(userId: string, returnUrl: string): Promise<{ url: string }>;
  createPortalSession(userId: string, returnUrl: string): Promise<{ url: string }>;
  updateSpendCap(userId: string, capCents: number | null): Promise<void>;
  setOnDemandEnabled(userId: string, enabled: boolean): Promise<void>;
}

export const DEFAULT_INCLUDED_CREDIT_CENTS = 500;
export const REFUND_WINDOW_DAYS = 31;
export const DEFAULT_SPEND_CAP_CENTS = 1500;
