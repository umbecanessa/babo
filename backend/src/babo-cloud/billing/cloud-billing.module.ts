import { Module } from '@nestjs/common';
import { ConfigModule, ConfigService } from '@nestjs/config';
import { PrismaModule } from '../../prisma/prisma.module';
import {
  CLOUD_BILLING_PROVIDER,
  CloudBillingProvider,
} from './cloud-billing.provider';
import { BillingController } from './billing.controller';
import { InternalCloudBillingProvider } from './internal-cloud-billing.provider';
import { NoOpCloudBillingProvider } from './noop-cloud-billing.provider';

export { CLOUD_BILLING_PROVIDER } from './cloud-billing.provider';
export type { CloudBillingProvider } from './cloud-billing.provider';
export { InternalCloudBillingProvider } from './internal-cloud-billing.provider';

const operatorMode = process.env.BILLING_PROVIDER === 'operator';

const billingProviderFactory = {
  provide: CLOUD_BILLING_PROVIDER,
  useFactory: (
    config: ConfigService,
    noop: NoOpCloudBillingProvider,
    internal: InternalCloudBillingProvider,
  ): CloudBillingProvider => {
    const cloudMode = config.get<string>('BABO_CLOUD_MODE') !== 'false';
    const provider = config.get<string>('BILLING_PROVIDER') || 'internal';

    if (!cloudMode || provider === 'noop') {
      return noop;
    }
    if (provider === 'operator') {
      throw new Error(
        'BILLING_PROVIDER=operator requires @babo/operator module (not loaded)',
      );
    }
    return internal;
  },
  inject: [ConfigService, NoOpCloudBillingProvider, InternalCloudBillingProvider],
};

@Module({
  imports: [PrismaModule, ConfigModule],
  controllers: [BillingController],
  providers: [
    NoOpCloudBillingProvider,
    InternalCloudBillingProvider,
    ...(operatorMode ? [] : [billingProviderFactory]),
  ],
  exports: operatorMode
    ? [NoOpCloudBillingProvider, InternalCloudBillingProvider]
    : [CLOUD_BILLING_PROVIDER, InternalCloudBillingProvider],
})
export class CloudBillingModule {}
