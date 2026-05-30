import {
  Body,
  Controller,
  Get,
  Inject,
  Post,
  Put,
  Query,
  Req,
  Res,
  UseGuards,
} from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import type { Response } from 'express';
import { JwtAuthGuard } from '../../auth/jwt-auth.guard';
import {
  CLOUD_BILLING_PROVIDER,
} from './cloud-billing.provider';
import type { CloudBillingProvider } from './cloud-billing.provider';
import {
  billingReturnEndpoint,
  inferBillingFlow,
  normalizeBillingReturnUrl,
} from './billing-return.util';

@Controller('billing')
export class BillingController {
  constructor(
    @Inject(CLOUD_BILLING_PROVIDER)
    private billing: CloudBillingProvider,
    private config: ConfigService,
  ) {}

  /** Stripe Checkout / Portal redirect target (HTTPS; desktop app uses file://). */
  @Get('return')
  billingReturn(
    @Query('status') status: string | undefined,
    @Query('flow') flow: string | undefined,
    @Res() res: Response,
  ) {
    const ok = status !== 'canceled';
    const title = ok ? 'Subscription updated' : 'Checkout canceled';
    const message = ok
      ? 'You can close this tab and return to the Babo desktop app. If you are still in setup, tap Continue on the billing step.'
      : 'No charge was made. Close this tab and return to Babo to try again or use your own API keys.';

    res.type('html').send(`<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>${title} · Babo</title>
  <style>
    body { font-family: system-ui, sans-serif; background: #0a0a0a; color: #e8e8e8;
      display: flex; min-height: 100vh; align-items: center; justify-content: center; margin: 0; }
    main { max-width: 28rem; padding: 2rem; text-align: center; }
    h1 { font-size: 1.35rem; font-weight: 600; margin: 0 0 0.75rem; }
    p { color: #a3a3a3; line-height: 1.55; margin: 0; font-size: 0.95rem; }
  </style>
</head>
<body>
  <main>
    <h1>${title}</h1>
    <p>${message}</p>
  </main>
</body>
</html>`);
  }

  @Post('checkout')
  @UseGuards(JwtAuthGuard)
  async checkout(
    @Req() req: any,
    @Body() body: { returnUrl?: string; flow?: 'setup' | 'settings' },
  ) {
    const flow =
      body?.flow === 'setup' || body?.flow === 'settings'
        ? body.flow
        : inferBillingFlow(body?.returnUrl);
    const returnUrl = normalizeBillingReturnUrl(
      this.config,
      body?.returnUrl,
      flow,
    );
    return this.billing.createCheckoutSession(req.user.userId, returnUrl);
  }

  @Post('portal')
  @UseGuards(JwtAuthGuard)
  async portal(
    @Req() req: any,
    @Body() body: { returnUrl?: string; flow?: 'setup' | 'settings' },
  ) {
    const returnUrl = normalizeBillingReturnUrl(
      this.config,
      body?.returnUrl,
      body?.flow === 'setup' ? 'setup' : 'settings',
    );
    return this.billing.createPortalSession(req.user.userId, returnUrl);
  }

  @Put('spend-cap')
  @UseGuards(JwtAuthGuard)
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
  @UseGuards(JwtAuthGuard)
  async onDemand(@Req() req: any, @Body() body: { enabled?: boolean }) {
    await this.billing.setOnDemandEnabled(req.user.userId, !!body?.enabled);
    return { ok: true };
  }
}