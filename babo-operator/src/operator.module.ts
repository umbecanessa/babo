import { DynamicModule, Global, Module } from '@nestjs/common';
import { ConfigModule } from '@nestjs/config';
import { CLOUD_BILLING_PROVIDER } from './contracts/cloud-billing';
import { StripeBillingProvider } from './billing/stripe-billing.provider';
import { StripeWebhookController } from './billing/stripe-webhook.controller';
import { StripeService } from './stripe/stripe.service';

function resolvePrismaService(): new (...args: unknown[]) => unknown {
  // Resolved at runtime from the host NestJS app (backend package export).
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  return require('backend/prisma').PrismaService;
}

@Global()
@Module({})
export class OperatorModule {
  static forRoot(): DynamicModule {
    const PrismaService = resolvePrismaService();

    return {
      module: OperatorModule,
      global: true,
      imports: [ConfigModule],
      controllers: [StripeWebhookController],
      providers: [
        StripeService,
        {
          provide: StripeBillingProvider,
          useFactory: (prisma: unknown, stripe: StripeService) =>
            new StripeBillingProvider(prisma as never, stripe),
          inject: [PrismaService, StripeService],
        },
        {
          provide: CLOUD_BILLING_PROVIDER,
          useExisting: StripeBillingProvider,
        },
      ],
      exports: [CLOUD_BILLING_PROVIDER, StripeBillingProvider],
    };
  }
}
