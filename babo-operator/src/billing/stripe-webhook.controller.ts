import {
  Controller,
  Headers,
  HttpException,
  HttpStatus,
  Post,
  Req,
} from '@nestjs/common';
import { StripeBillingProvider } from './stripe-billing.provider';
import { StripeService } from '../stripe/stripe.service';

@Controller('billing/stripe')
export class StripeWebhookController {
  constructor(
    private stripe: StripeService,
    private billing: StripeBillingProvider,
  ) {}

  @Post('webhook')
  async webhook(
    @Headers('stripe-signature') signature: string,
    @Req() req: { rawBody?: Buffer },
  ) {
    if (!signature) {
      throw new HttpException('Missing stripe-signature', HttpStatus.BAD_REQUEST);
    }
    if (!this.stripe.webhookSecret) {
      throw new HttpException(
        'STRIPE_WEBHOOK_SECRET not configured',
        HttpStatus.INTERNAL_SERVER_ERROR,
      );
    }

    const rawBody = req.rawBody;
    if (!rawBody) {
      throw new HttpException('Raw body required', HttpStatus.BAD_REQUEST);
    }

    let event;
    try {
      event = this.stripe.client.webhooks.constructEvent(
        rawBody,
        signature,
        this.stripe.webhookSecret,
      );
    } catch (err: any) {
      throw new HttpException(
        `Webhook signature verification failed: ${err.message}`,
        HttpStatus.BAD_REQUEST,
      );
    }

    await this.billing.handleStripeEvent(event as any);
    return { received: true };
  }
}
