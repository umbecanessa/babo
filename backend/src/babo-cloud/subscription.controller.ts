import { Body, Controller, Get, Post, Query, Req, UseGuards } from '@nestjs/common';
import { JwtAuthGuard } from '../auth/jwt-auth.guard';
import { CloudUsageService } from './cloud-usage.service';
import { EntitlementsService } from './entitlements.service';

@Controller('cloud')
@UseGuards(JwtAuthGuard)
export class SubscriptionController {
  constructor(
    private entitlements: EntitlementsService,
    private usage: CloudUsageService,
  ) {}

  /** Per-request inference ledger + subscription totals (verify Babo Cloud metering). */
  @Get('usage')
  async getUsage(@Req() req: any, @Query('limit') limit?: string) {
    const n = limit ? parseInt(limit, 10) : 25;
    return this.usage.listForUser(req.user.userId, Number.isFinite(n) ? n : 25);
  }

  @Get('subscription')
  async getSubscription(@Req() req: any) {
    const sub = await this.entitlements.getSubscription(req.user.userId);
    if (!sub) {
      return { status: 'none', cloudMode: this.entitlements.isCloudMode() };
    }
    return {
      status: sub.status,
      planId: sub.planId,
      trialEndsAt: sub.trialEndsAt,
      currentPeriodEnd: sub.currentPeriodEnd,
      includedTokens: sub.includedTokens,
      usedTokens: sub.usedTokens,
      allowOverage: sub.allowOverage,
      cloudMode: this.entitlements.isCloudMode(),
    };
  }

  /** Stub for Stripe webhook / admin — activates paid plan. */
  @Post('subscription/activate')
  async activate(@Req() req: any, @Body() body: { planId?: string }) {
    await this.entitlements.activatePaid(
      req.user.userId,
      body.planId || 'cloud_basic',
    );
    return { ok: true };
  }
}
