import { Module } from '@nestjs/common';
import { ConfigModule } from '@nestjs/config';
import { PrismaModule } from '../prisma/prisma.module';
import { CloudBillingModule } from './billing/cloud-billing.module';
import { EntitlementsService } from './entitlements.service';

@Module({
  imports: [PrismaModule, ConfigModule, CloudBillingModule],
  providers: [EntitlementsService],
  exports: [EntitlementsService, CloudBillingModule],
})
export class EntitlementsModule {}
