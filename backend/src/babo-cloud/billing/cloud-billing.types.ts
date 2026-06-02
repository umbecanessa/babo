/** Paid plan ids (operator may extend). */
export type CloudPlanId = 'cloud_basic' | 'lifetime_comp' | string;

export type CloudSubscriptionStatus =
  | 'none'
  | 'active'
  | 'past_due'
  | 'canceled'
  | 'lifetime_comp';

export interface SubscriptionView {
  billingEnabled: boolean;
  status: CloudSubscriptionStatus;
  planId: CloudPlanId | null;
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

export const DEFAULT_INCLUDED_CREDIT_CENTS = 500;
export const REFUND_WINDOW_DAYS = 31;
