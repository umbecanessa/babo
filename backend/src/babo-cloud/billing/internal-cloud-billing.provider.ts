import {
  HttpException,
  HttpStatus,
  Injectable,
  Logger,
} from '@nestjs/common';
import { PrismaService } from '../../prisma/prisma.service';
import { CloudBillingProvider } from './cloud-billing.provider';
import {
  CLOUD_BASIC_MONTHLY_PRICE_LABEL,
  DEFAULT_INCLUDED_CREDIT_CENTS,
  REFUND_WINDOW_DAYS,
  SubscriptionView,
} from './cloud-billing.types';

function usedPercent(used: number, included: number): number {
  if (included <= 0) return used > 0 ? 100 : 0;
  return Math.min(100, Math.round((used / included) * 100));
}

function refundEligibleUntil(firstPaidAt: Date | null): Date | null {
  if (!firstPaidAt) return null;
  const end = new Date(firstPaidAt);
  end.setDate(end.getDate() + REFUND_WINDOW_DAYS);
  return end;
}

@Injectable()
export class InternalCloudBillingProvider implements CloudBillingProvider {
  private readonly logger = new Logger(InternalCloudBillingProvider.name);

  constructor(private prisma: PrismaService) {}

  isEnabled(): boolean {
    return true;
  }

  async onUserRegistered(userId: string): Promise<void> {
    const existing = await this.prisma.cloudSubscription.findUnique({
      where: { userId },
    });
    if (existing) return;

    await this.prisma.cloudSubscription.create({
      data: {
        userId,
        status: 'none',
        planId: 'none',
        includedCreditCents: 0,
        usedCreditCents: 0,
      },
    });
    this.logger.log(`Cloud subscription row created for ${userId}`);
  }

  async assertCloudAccess(userId: string): Promise<void> {
    const sub = await this.prisma.cloudSubscription.findUnique({
      where: { userId },
    });
    if (!sub) {
      throw new HttpException(
        'Babo Cloud subscription required — subscribe at Settings → Billing',
        HttpStatus.PAYMENT_REQUIRED,
      );
    }

    if (sub.billingExempt || sub.planId === 'lifetime_comp') {
      return;
    }

    if (sub.status === 'canceled') {
      throw new HttpException(
        'Subscription canceled — renew to use Babo Cloud',
        HttpStatus.PAYMENT_REQUIRED,
      );
    }

    if (sub.status === 'none') {
      throw new HttpException(
        `Subscribe to Babo Cloud (${CLOUD_BASIC_MONTHLY_PRICE_LABEL}) to use hosted models`,
        HttpStatus.PAYMENT_REQUIRED,
      );
    }

    const now = new Date();
    if (
      sub.status === 'active' &&
      sub.currentPeriodEnd &&
      sub.currentPeriodEnd < now
    ) {
      throw new HttpException(
        'Subscription period ended',
        HttpStatus.PAYMENT_REQUIRED,
      );
    }

    if (sub.status === 'past_due') {
      throw new HttpException(
        'Payment failed — update billing to continue',
        HttpStatus.PAYMENT_REQUIRED,
      );
    }

    if (
      !sub.allowOverage &&
      sub.includedCreditCents > 0 &&
      sub.usedCreditCents >= sub.includedCreditCents
    ) {
      throw new HttpException(
        'Included usage exhausted — enable pay-as-you-go or wait for renewal',
        HttpStatus.PAYMENT_REQUIRED,
      );
    }
  }

  async recordUsage(
    userId: string,
    upstreamCostCents: number,
    opts?: { placement?: string },
  ): Promise<void> {
    if (upstreamCostCents <= 0) return;

    const placement = opts?.placement ?? '';
    if (placement === 'byok_cloud') return;

    const sub = await this.prisma.cloudSubscription.findUnique({
      where: { userId },
    });
    if (!sub || sub.billingExempt) return;

    // GX10 comp path — no pool debit in v1
    if (sub.hostedGx10Enabled && placement === 'hosted_babo') return;

    await this.prisma.cloudSubscription.updateMany({
      where: { userId },
      data: { usedCreditCents: { increment: upstreamCostCents } },
    });
  }

  async createCheckoutSession(): Promise<{ url: string }> {
    throw new HttpException(
      'Stripe checkout requires BILLING_PROVIDER=operator — set STRIPE_* keys and install @babo/operator',
      HttpStatus.NOT_IMPLEMENTED,
    );
  }

  async createPortalSession(): Promise<{ url: string }> {
    throw new HttpException(
      'Billing portal requires BILLING_PROVIDER=operator',
      HttpStatus.NOT_IMPLEMENTED,
    );
  }

  async updateSpendCap(userId: string, capCents: number | null): Promise<void> {
    await this.prisma.cloudSubscription.updateMany({
      where: { userId },
      data: { monthlySpendCapCents: capCents },
    });
  }

  async setOnDemandEnabled(userId: string, enabled: boolean): Promise<void> {
    await this.prisma.cloudSubscription.updateMany({
      where: { userId },
      data: { onDemandEnabled: enabled },
    });
  }

  async syncSubscription(userId: string): Promise<SubscriptionView> {
    return this.getSubscriptionView(userId);
  }

  async getSubscriptionView(userId: string): Promise<SubscriptionView> {
    const sub = await this.prisma.cloudSubscription.findUnique({
      where: { userId },
    });
    if (!sub) {
      return {
        billingEnabled: true,
        status: 'none',
        planId: null,
        billingExempt: false,
        hostedGx10Enabled: false,
        includedCreditCents: 0,
        usedCreditCents: 0,
        usedPercent: 0,
        currentPeriodEnd: null,
        firstPaidAt: null,
        allowOverage: true,
        monthlySpendCapCents: 1500,
        onDemandEnabled: false,
        refundEligibleUntil: null,
      };
    }

    const refundUntil = refundEligibleUntil(sub.firstPaidAt);

    return {
      billingEnabled: true,
      status: sub.status as SubscriptionView['status'],
      planId: sub.planId,
      billingExempt: sub.billingExempt,
      hostedGx10Enabled: sub.hostedGx10Enabled,
      includedCreditCents: sub.includedCreditCents,
      usedCreditCents: sub.usedCreditCents,
      usedPercent: usedPercent(sub.usedCreditCents, sub.includedCreditCents),
      currentPeriodEnd: sub.currentPeriodEnd?.toISOString() ?? null,
      firstPaidAt: sub.firstPaidAt?.toISOString() ?? null,
      allowOverage: sub.allowOverage,
      monthlySpendCapCents: sub.monthlySpendCapCents,
      onDemandEnabled: sub.onDemandEnabled,
      refundEligibleUntil:
        refundUntil && refundUntil > new Date()
          ? refundUntil.toISOString()
          : null,
    };
  }

  /** Dev / pre-operator: activate paid plan without Stripe. Operator replaces this. */
  async activatePaidPlan(
    userId: string,
    planId = 'cloud_basic',
    includedCreditCents = DEFAULT_INCLUDED_CREDIT_CENTS,
  ): Promise<void> {
    const periodEnd = new Date();
    periodEnd.setMonth(periodEnd.getMonth() + 1);
    const now = new Date();

    await this.prisma.cloudSubscription.upsert({
      where: { userId },
      create: {
        userId,
        status: 'active',
        planId,
        currentPeriodEnd: periodEnd,
        firstPaidAt: now,
        includedCreditCents,
        usedCreditCents: 0,
        allowOverage: true,
        onDemandEnabled: false,
        monthlySpendCapCents: 1500,
      },
      update: {
        status: 'active',
        planId,
        currentPeriodEnd: periodEnd,
        firstPaidAt: now,
        includedCreditCents,
        usedCreditCents: 0,
        allowOverage: true,
      },
    });
  }

  async grantLifetimeComp(
    userId: string,
    grantedByAdminId: string,
    grantNote?: string,
  ): Promise<void> {
    await this.prisma.cloudSubscription.upsert({
      where: { userId },
      create: {
        userId,
        status: 'lifetime_comp',
        planId: 'lifetime_comp',
        billingExempt: true,
        hostedGx10Enabled: true,
        grantedByAdminId,
        grantNote: grantNote ?? null,
        includedCreditCents: 0,
        usedCreditCents: 0,
        allowOverage: false,
      },
      update: {
        status: 'lifetime_comp',
        planId: 'lifetime_comp',
        billingExempt: true,
        hostedGx10Enabled: true,
        grantedByAdminId,
        grantNote: grantNote ?? null,
        allowOverage: false,
      },
    });
  }

  async revokeLifetimeComp(userId: string): Promise<void> {
    await this.prisma.cloudSubscription.updateMany({
      where: { userId },
      data: {
        status: 'none',
        planId: 'none',
        billingExempt: false,
        hostedGx10Enabled: false,
        grantedByAdminId: null,
        grantNote: null,
        includedCreditCents: 0,
        usedCreditCents: 0,
      },
    });
  }
}
