import {
  HttpException,
  HttpStatus,
  Injectable,
  Logger,
} from '@nestjs/common';
import { randomBytes } from 'crypto';
import {
  CLOUD_BILLING_PROVIDER,
  CloudBillingProvider,
  DEFAULT_INCLUDED_CREDIT_CENTS,
  DEFAULT_SPEND_CAP_CENTS,
  REFUND_WINDOW_DAYS,
  SubscriptionView,
} from '../contracts/cloud-billing';
import { StripeService } from '../stripe/stripe.service';

/** Minimal prisma surface used by billing (duck-typed for cross-package DI). */
export type BillingPrisma = {
  user: { findUnique: (args: any) => Promise<any> };
  cloudSubscription: {
    findUnique: (args: any) => Promise<any>;
    findFirst: (args: any) => Promise<any>;
    create: (args: any) => Promise<any>;
    update: (args: any) => Promise<any>;
    updateMany: (args: any) => Promise<any>;
  };
  stripeWebhookEvent: {
    findUnique: (args: any) => Promise<any>;
    create: (args: any) => Promise<any>;
  };
};

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

function appendBillingQuery(url: string, status: string): string {
  const sep = url.includes('?') ? '&' : '?';
  return `${url}${sep}billing=${status}`;
}

function referralCode(): string {
  return randomBytes(4).toString('hex');
}

@Injectable()
export class StripeBillingProvider implements CloudBillingProvider {
  private readonly logger = new Logger(StripeBillingProvider.name);

  constructor(
    private prisma: BillingPrisma,
    private stripe: StripeService,
  ) {}

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
        referralCode: referralCode(),
        monthlySpendCapCents: DEFAULT_SPEND_CAP_CENTS,
      },
    });
    this.logger.log(`Billing row created for ${userId}`);
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
        'Subscription canceled — renew to continue',
        HttpStatus.PAYMENT_REQUIRED,
      );
    }

    if (sub.status === 'none') {
      throw new HttpException(
        'Subscribe to Babo Cloud ($6.99/mo) to use hosted models',
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
      !sub.onDemandEnabled &&
      sub.includedCreditCents > 0 &&
      sub.usedCreditCents >= sub.includedCreditCents
    ) {
      throw new HttpException(
        'Included usage exhausted — enable on-demand or wait for renewal',
        HttpStatus.PAYMENT_REQUIRED,
      );
    }

    if (
      sub.onDemandEnabled &&
      sub.monthlySpendCapCents != null &&
      sub.usedCreditCents >= sub.includedCreditCents
    ) {
      const overageCents =
        sub.usedCreditCents - sub.includedCreditCents;
      const overageBillCents = Math.ceil(overageCents * 1.25);
      if (overageBillCents >= sub.monthlySpendCapCents) {
        throw new HttpException(
          'Monthly spend cap reached — increase cap in Settings → Billing',
          HttpStatus.PAYMENT_REQUIRED,
        );
      }
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
    if (sub.hostedGx10Enabled && placement === 'hosted_babo') return;

    await this.prisma.cloudSubscription.updateMany({
      where: { userId },
      data: { usedCreditCents: { increment: upstreamCostCents } },
    });
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
        monthlySpendCapCents: DEFAULT_SPEND_CAP_CENTS,
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

  async createCheckoutSession(
    userId: string,
    returnUrl: string,
  ): Promise<{ url: string }> {
    const user = await this.prisma.user.findUnique({ where: { id: userId } });
    if (!user) {
      throw new HttpException('User not found', HttpStatus.NOT_FOUND);
    }

    let sub = await this.prisma.cloudSubscription.findUnique({
      where: { userId },
    });
    if (!sub) {
      await this.onUserRegistered(userId);
      sub = await this.prisma.cloudSubscription.findUnique({
        where: { userId },
      });
    }
    if (sub?.billingExempt || sub?.planId === 'lifetime_comp') {
      throw new HttpException(
        'Lifetime access — no subscription needed',
        HttpStatus.BAD_REQUEST,
      );
    }

    let customerId = sub?.stripeCustomerId ?? null;
    if (!customerId) {
      const customer = await this.stripe.client.customers.create({
        email: user.email,
        name: user.displayName || undefined,
        metadata: { userId },
      });
      customerId = customer.id;
      await this.prisma.cloudSubscription.update({
        where: { userId },
        data: { stripeCustomerId: customerId },
      });
    }

    const session = await this.stripe.client.checkout.sessions.create({
      mode: 'subscription',
      customer: customerId,
      line_items: [{ price: this.stripe.priceId, quantity: 1 }],
      success_url: appendBillingQuery(returnUrl, 'success'),
      cancel_url: appendBillingQuery(returnUrl, 'canceled'),
      allow_promotion_codes: true,
      client_reference_id: userId,
      metadata: {
        userId,
        referral_code: sub?.referralCode ?? '',
      },
      subscription_data: {
        metadata: { userId, planId: 'cloud_basic' },
      },
    });

    if (!session.url) {
      throw new HttpException(
        'Could not create checkout session',
        HttpStatus.BAD_GATEWAY,
      );
    }

    return { url: session.url };
  }

  async createPortalSession(
    userId: string,
    returnUrl: string,
  ): Promise<{ url: string }> {
    const sub = await this.prisma.cloudSubscription.findUnique({
      where: { userId },
    });
    if (!sub?.stripeCustomerId) {
      throw new HttpException(
        'No billing account — subscribe first',
        HttpStatus.BAD_REQUEST,
      );
    }

    const session = await this.stripe.client.billingPortal.sessions.create({
      customer: sub.stripeCustomerId,
      return_url: returnUrl,
    });

    return { url: session.url };
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

  /** Called from webhook controller after signature verification. */
  async handleStripeEvent(event: { id: string; type: string; data: { object: any } }) {
    const seen = await this.prisma.stripeWebhookEvent.findUnique({
      where: { id: event.id },
    });
    if (seen) {
      this.logger.debug(`Skipping duplicate webhook ${event.id}`);
      return;
    }

    switch (event.type) {
      case 'checkout.session.completed':
        await this.onCheckoutCompleted(event.data.object);
        break;
      case 'customer.subscription.updated':
        await this.onSubscriptionUpdated(event.data.object);
        break;
      case 'customer.subscription.deleted':
        await this.onSubscriptionDeleted(event.data.object);
        break;
      case 'invoice.paid':
        await this.onInvoicePaid(event.data.object);
        break;
      case 'invoice.payment_failed':
        await this.onInvoicePaymentFailed(event.data.object);
        break;
      default:
        this.logger.debug(`Unhandled Stripe event ${event.type}`);
    }

    await this.prisma.stripeWebhookEvent.create({
      data: { id: event.id, type: event.type },
    });
  }

  private async onCheckoutCompleted(session: any) {
    const userId =
      session.client_reference_id ||
      session.metadata?.userId ||
      session.subscription_data?.metadata?.userId;
    if (!userId) {
      this.logger.warn('checkout.session.completed without userId');
      return;
    }

    const subscriptionId =
      typeof session.subscription === 'string'
        ? session.subscription
        : session.subscription?.id;

    const periodEnd = new Date();
    periodEnd.setMonth(periodEnd.getMonth() + 1);

    await this.prisma.cloudSubscription.update({
      where: { userId },
      data: {
        status: 'active',
        planId: 'cloud_basic',
        stripeCustomerId: session.customer as string,
        stripeSubscriptionId: subscriptionId ?? undefined,
        includedCreditCents: DEFAULT_INCLUDED_CREDIT_CENTS,
        usedCreditCents: 0,
        currentPeriodEnd: periodEnd,
        allowOverage: true,
        monthlySpendCapCents: DEFAULT_SPEND_CAP_CENTS,
      },
    });
    this.logger.log(`Activated cloud_basic for ${userId}`);
  }

  private async onSubscriptionUpdated(sub: any) {
    const userId = sub.metadata?.userId;
    if (!userId) return;

    const status = this.mapStripeStatus(sub.status);
    await this.prisma.cloudSubscription.updateMany({
      where: { userId },
      data: {
        status,
        stripeSubscriptionId: sub.id,
        currentPeriodEnd: sub.current_period_end
          ? new Date(sub.current_period_end * 1000)
          : undefined,
      },
    });
  }

  private async onSubscriptionDeleted(sub: any) {
    const userId = sub.metadata?.userId;
    if (!userId) return;

    await this.prisma.cloudSubscription.updateMany({
      where: { userId },
      data: {
        status: 'canceled',
        stripeSubscriptionId: null,
        includedCreditCents: 0,
      },
    });
  }

  private async onInvoicePaid(invoice: any) {
    const customerId =
      typeof invoice.customer === 'string'
        ? invoice.customer
        : invoice.customer?.id;
    if (!customerId) return;

    const sub = await this.prisma.cloudSubscription.findFirst({
      where: { stripeCustomerId: customerId },
    });
    if (!sub) return;

    const periodEnd = invoice.lines?.data?.[0]?.period?.end;
    const data: Record<string, unknown> = {
      usedCreditCents: 0,
      status: 'active',
    };
    if (periodEnd) {
      data.currentPeriodEnd = new Date(periodEnd * 1000);
    }
    if (!sub.firstPaidAt && invoice.amount_paid > 0) {
      data.firstPaidAt = new Date();
    }

    await this.prisma.cloudSubscription.update({
      where: { userId: sub.userId },
      data,
    });
  }

  private async onInvoicePaymentFailed(invoice: any) {
    const customerId =
      typeof invoice.customer === 'string'
        ? invoice.customer
        : invoice.customer?.id;
    if (!customerId) return;

    await this.prisma.cloudSubscription.updateMany({
      where: { stripeCustomerId: customerId },
      data: { status: 'past_due' },
    });
  }

  private mapStripeStatus(stripeStatus: string): string {
    switch (stripeStatus) {
      case 'active':
      case 'trialing':
        return 'active';
      case 'past_due':
      case 'unpaid':
        return 'past_due';
      case 'canceled':
      case 'incomplete_expired':
        return 'canceled';
      default:
        return 'none';
    }
  }
}

export { CLOUD_BILLING_PROVIDER };
