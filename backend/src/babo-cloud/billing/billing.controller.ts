import {

  Body,

  Controller,

  Inject,

  Post,

  Put,

  Req,

  UseGuards,

} from '@nestjs/common';

import { JwtAuthGuard } from '../../auth/jwt-auth.guard';

import {
  CLOUD_BILLING_PROVIDER,
} from './cloud-billing.provider';
import type { CloudBillingProvider } from './cloud-billing.provider';



@Controller('billing')

@UseGuards(JwtAuthGuard)

export class BillingController {

  constructor(

    @Inject(CLOUD_BILLING_PROVIDER)

    private billing: CloudBillingProvider,

  ) {}



  @Post('checkout')

  async checkout(@Req() req: any, @Body() body: { returnUrl?: string }) {

    const returnUrl =

      body?.returnUrl?.trim() ||

      `${req.headers.origin || ''}/settings?section=billing`;

    return this.billing.createCheckoutSession(req.user.userId, returnUrl);

  }



  @Post('portal')

  async portal(@Req() req: any, @Body() body: { returnUrl?: string }) {

    const returnUrl =

      body?.returnUrl?.trim() ||

      `${req.headers.origin || ''}/settings?section=billing`;

    return this.billing.createPortalSession(req.user.userId, returnUrl);

  }



  @Put('spend-cap')

  async spendCap(

    @Req() req: any,

    @Body() body: { capCents?: number | null },

  ) {

    const cap =

      body?.capCents === null || body?.capCents === undefined

        ? null

        : Math.max(0, Math.round(body.capCents));

    await this.billing.updateSpendCap(req.user.userId, cap);

    return { ok: true };

  }



  @Put('on-demand')

  async onDemand(@Req() req: any, @Body() body: { enabled?: boolean }) {

    await this.billing.setOnDemandEnabled(req.user.userId, !!body?.enabled);

    return { ok: true };

  }

}


