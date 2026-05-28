import {
  HttpException,
  HttpStatus,
  Injectable,
  Logger,
} from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { PrismaService } from '../prisma/prisma.service';

const TRIAL_DAYS = 30;
const DEFAULT_INCLUDED_TOKENS = 500_000;

@Injectable()
export class EntitlementsService {
  private readonly logger = new Logger(EntitlementsService.name);
  private readonly cloudMode: boolean;

  constructor(
    private prisma: PrismaService,
    config: ConfigService,
  ) {
    this.cloudMode = config.get<string>('BABO_CLOUD_MODE') !== 'false';
  }

  isCloudMode(): boolean {
    return this.cloudMode;
  }

  /** Create trialing subscription for new Babo Cloud users. */
  async ensureSubscriptionForUser(userId: string): Promise<void> {
    if (!this.cloudMode) return;
    const existing = await this.prisma.cloudSubscription.findUnique({
      where: { userId },
    });
    if (existing) return;

    const trialEnds = new Date();
    trialEnds.setDate(trialEnds.getDate() + TRIAL_DAYS);

    await this.prisma.cloudSubscription.create({
      data: {
        userId,
        status: 'trialing',
        planId: 'cloud_basic',
        trialEndsAt: trialEnds,
        currentPeriodEnd: trialEnds,
        includedTokens: DEFAULT_INCLUDED_TOKENS,
        usedTokens: 0,
        allowOverage: true,
      },
    });
    this.logger.log(`Cloud trial started for user ${userId}`);
  }

  async assertCloudAccess(userId: string): Promise<void> {
    if (!this.cloudMode) return;

    const sub = await this.prisma.cloudSubscription.findUnique({
      where: { userId },
    });
    if (!sub) {
      throw new HttpException(
        'Babo Cloud subscription required',
        HttpStatus.PAYMENT_REQUIRED,
      );
    }

    const now = new Date();
    if (sub.status === 'canceled') {
      throw new HttpException(
        'Subscription canceled — renew to use Babo Cloud',
        HttpStatus.PAYMENT_REQUIRED,
      );
    }

    if (sub.status === 'trialing' && sub.trialEndsAt && sub.trialEndsAt < now) {
      throw new HttpException(
        'Trial ended — subscribe to continue using Babo Cloud',
        HttpStatus.PAYMENT_REQUIRED,
      );
    }

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

    if (
      !sub.allowOverage &&
      sub.usedTokens >= sub.includedTokens &&
      sub.status !== 'trialing'
    ) {
      throw new HttpException(
        'Included token quota exhausted',
        HttpStatus.PAYMENT_REQUIRED,
      );
    }
  }

  async addTokenUsage(userId: string, delta: number): Promise<void> {
    if (!this.cloudMode || delta <= 0) return;
    await this.prisma.cloudSubscription.updateMany({
      where: { userId },
      data: { usedTokens: { increment: delta } },
    });
  }

  async getSubscription(userId: string) {
    return this.prisma.cloudSubscription.findUnique({ where: { userId } });
  }

  /** Admin / webhook: activate paid subscription. */
  async activatePaid(
    userId: string,
    planId = 'cloud_basic',
    includedTokens = DEFAULT_INCLUDED_TOKENS,
  ): Promise<void> {
    const periodEnd = new Date();
    periodEnd.setMonth(periodEnd.getMonth() + 1);
    await this.prisma.cloudSubscription.upsert({
      where: { userId },
      create: {
        userId,
        status: 'active',
        planId,
        currentPeriodEnd: periodEnd,
        includedTokens,
        usedTokens: 0,
        allowOverage: true,
      },
      update: {
        status: 'active',
        planId,
        currentPeriodEnd: periodEnd,
        includedTokens,
        allowOverage: true,
      },
    });
  }
}
