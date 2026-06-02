import { Module } from '@nestjs/common';
import { MulterModule } from '@nestjs/platform-express';
import { ApiKeysModule } from '../api-keys/api-keys.module';
import { AuthModule } from '../auth/auth.module';
import { PrismaModule } from '../prisma/prisma.module';
import { SettingsModule } from '../settings/settings.module';
import { CloudProvidersController } from './cloud-providers.controller';
import { CloudAccessGuard } from './cloud-access.guard';
import { CloudAuthGuard } from './cloud-auth.guard';
import { CloudRateLimiterService } from './cloud-rate-limiter.service';
import { CloudUpstreamService } from './cloud-upstream.service';
import { CloudUsageService } from './cloud-usage.service';
import { CryptoService } from './crypto.service';
import { EntitlementsModule } from './entitlements.module';
import { GpuController } from './gpu.controller';
import { GpuService } from './gpu.service';
import { InferenceController } from './inference.controller';
import { InferenceService } from './inference.service';
import { ProviderKeysService } from './provider-keys.service';
import { PlatformCapabilitiesController } from './platform-capabilities.controller';
import { SubscriptionController } from './subscription.controller';

@Module({
  imports: [
    PrismaModule,
    ApiKeysModule,
    AuthModule,
    EntitlementsModule,
    SettingsModule,
    MulterModule.register({
      limits: { fileSize: 25 * 1024 * 1024 },
    }),
  ],
  controllers: [
    InferenceController,
    GpuController,
    CloudProvidersController,
    PlatformCapabilitiesController,
    SubscriptionController,
  ],
  providers: [
    CloudUpstreamService,
    CloudRateLimiterService,
    CloudUsageService,
    CryptoService,
    ProviderKeysService,
    InferenceService,
    GpuService,
    CloudAuthGuard,
    CloudAccessGuard,
  ],
  exports: [EntitlementsModule, ProviderKeysService, CloudUpstreamService],
})
export class BaboCloudModule {}
