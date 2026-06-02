import { Controller, Get, Query, Req, UseGuards } from '@nestjs/common';
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

  @Get('usage')
  async getUsage(@Req() req: any, @Query('limit') limit?: string) {
    const n = limit ? parseInt(limit, 10) : 25;
    return this.usage.listForUser(req.user.userId, Number.isFinite(n) ? n : 25);
  }

  @Get('subscription')
  async getSubscription(@Req() req: any) {
    const view = await this.entitlements.getSubscriptionView(req.user.userId);
    return {
      ...view,
      cloudMode: this.entitlements.isCloudMode(),
    };
  }
}
