import { Injectable } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import Stripe from 'stripe';

export interface StripeConfig {
  secretKey: string;
  webhookSecret: string;
  priceId: string;
  portalReturnUrl?: string;
}

@Injectable()
export class StripeService {
  readonly client: Stripe;
  readonly webhookSecret: string;
  readonly priceId: string;

  constructor(config: ConfigService) {
    const secretKey = config.get<string>('STRIPE_SECRET_KEY') || '';
    this.webhookSecret = config.get<string>('STRIPE_WEBHOOK_SECRET') || '';
    this.priceId = config.get<string>('STRIPE_PRICE_CLOUD_BASIC') || '';

    if (!secretKey) {
      throw new Error('STRIPE_SECRET_KEY is required when BILLING_PROVIDER=operator');
    }
    if (!this.priceId) {
      throw new Error('STRIPE_PRICE_CLOUD_BASIC is required when BILLING_PROVIDER=operator');
    }

    this.client = new Stripe(secretKey);
  }
}
