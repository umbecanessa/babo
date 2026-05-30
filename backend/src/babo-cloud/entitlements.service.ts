import { Inject, Injectable } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { PrismaService } from '../prisma/prisma.service';
import {
  CLOUD_BILLING_PROVIDER,
} from './billing/cloud-billing.provider';
import type { CloudBillingProvider } from './billing/cloud-billing.provider';
import { SubscriptionView } from './billing/cloud-billing.types';

@Injectable()
export class EntitlementsService {
  private readonly cloudMode: boolean;

  constructor(
    private prisma: PrismaService,
    config: ConfigService,
    @Inject(CLOUD_BILLING_PROVIDER)
    private billing: CloudBillingProvider,
  ) {
    this.cloudMode = config.get<string>('BABO_CLOUD_MODE') !== 'false';
  }

  isCloudMode(): boolean {
    return this.cloudMode;
  }

  billingEnabled(): boolean {
    return this.billing.isEnabled();
  }

  async onUserRegistered(userId: string): Promise<void> {
    if (!this.cloudMode) return;
    await this.billing.onUserRegistered(userId);
  }

  /** @deprecated use onUserRegistered */
  async ensureSubscriptionForUser(userId: string): Promise<void> {
    return this.onUserRegistered(userId);
  }

  async assertCloudAccess(userId: string): Promise<void> {
    if (!this.cloudMode) return;
    await this.billing.assertCloudAccess(userId);
  }

  async recordUsage(
    userId: string,
    upstreamCostCents: number,
    opts?: { placement?: string },
  ): Promise<void> {
    if (!this.cloudMode || upstreamCostCents <= 0) return;
    await this.billing.recordUsage(userId, upstreamCostCents, opts);
  }

  async getSubscriptionView(userId: string): Promise<SubscriptionView> {
    if (!this.cloudMode) {
      return this.billing.getSubscriptionView(userId);
    }
    return this.billing.getSubscriptionView(userId);
  }

  async getSubscription(userId: string) {
    return this.prisma.cloudSubscription.findUnique({ where: { userId } });
  }

  async getHostedGx10Enabled(userId: string): Promise<boolean> {
    const sub = await this.prisma.cloudSubscription.findUnique({
      where: { userId },
      select: { hostedGx10Enabled: true, billingExempt: true },
    });
    return !!sub?.hostedGx10Enabled;
  }
}
