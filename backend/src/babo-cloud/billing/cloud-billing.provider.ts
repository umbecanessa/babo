import { SubscriptionView } from './cloud-billing.types';

/** String token so @babo/operator can override the same provider. */
export const CLOUD_BILLING_PROVIDER = 'CLOUD_BILLING_PROVIDER';

export interface CloudBillingProvider {
  isEnabled(): boolean;

  assertCloudAccess(userId: string): Promise<void>;

  onUserRegistered(userId: string): Promise<void>;

  /** Track metered usage for pay-as-you-go (skip when billingExempt or non-billable placement). */
  recordUsage(
    userId: string,
    upstreamCostCents: number,
    opts?: { placement?: string },
  ): Promise<void>;

  getSubscriptionView(userId: string): Promise<SubscriptionView>;

  /** Stripe Checkout (Link enabled in Dashboard). Requires operator in production. */
  createCheckoutSession(
    userId: string,
    returnUrl: string,
  ): Promise<{ url: string }>;

  createPortalSession(userId: string, returnUrl: string): Promise<{ url: string }>;

  updateSpendCap(userId: string, capCents: number | null): Promise<void>;

  setOnDemandEnabled(userId: string, enabled: boolean): Promise<void>;

  /** Pull active Stripe subscription state (operator only; no-op otherwise). */
  syncSubscription?(userId: string): Promise<SubscriptionView>;
}
