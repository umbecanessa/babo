import { Body, Controller, Get, Post, Req, UseGuards } from '@nestjs/common';
import { JwtAuthGuard } from '../auth/jwt-auth.guard';
import { EntitlementsService } from './entitlements.service';

@Controller('cloud')
@UseGuards(JwtAuthGuard)
export class SubscriptionController {
  constructor(private entitlements: EntitlementsService) {}

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
