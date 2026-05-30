import { HttpException, HttpStatus, Injectable } from '@nestjs/common';
import {
  CloudBillingProvider,
} from './cloud-billing.provider';
import { SubscriptionView } from './cloud-billing.types';

function billingUnavailable(): never {
  throw new HttpException('Billing is not enabled on this server', HttpStatus.NOT_FOUND);
}

@Injectable()
export class NoOpCloudBillingProvider implements CloudBillingProvider {
  isEnabled(): boolean {
    return false;
  }

  async assertCloudAccess(_userId: string): Promise<void> {}

  async onUserRegistered(_userId: string): Promise<void> {}

  async recordUsage(
    _userId: string,
    _upstreamCostCents: number,
  ): Promise<void> {}

  async createCheckoutSession(): Promise<{ url: string }> {
    billingUnavailable();
  }

  async createPortalSession(): Promise<{ url: string }> {
    billingUnavailable();
  }

  async updateSpendCap(): Promise<void> {
    billingUnavailable();
  }

  async setOnDemandEnabled(): Promise<void> {
    billingUnavailable();
  }

  async getSubscriptionView(_userId: string): Promise<SubscriptionView> {
    return {
      billingEnabled: false,
      status: 'none',
      planId: null,
      billingExempt: false,
      hostedGx10Enabled: false,
      includedCreditCents: 0,
      usedCreditCents: 0,
      usedPercent: 0,
      currentPeriodEnd: null,
      firstPaidAt: null,
      allowOverage: false,
      monthlySpendCapCents: null,
      onDemandEnabled: false,
      refundEligibleUntil: null,
    };
  }
}
