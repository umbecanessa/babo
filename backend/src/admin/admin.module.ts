import { Module } from '@nestjs/common';
import { AdminController } from './admin.controller';
import { AdminSetupController } from './admin-setup.controller';
import { AdminService } from './admin.service';
import { RuntimeModule } from '../runtime/runtime.module';
import { AuthModule } from '../auth/auth.module';
import { EntitlementsModule } from '../babo-cloud/entitlements.module';
import { AnalyticsModule } from '../analytics/analytics.module';

@Module({
  imports: [RuntimeModule, AuthModule, EntitlementsModule, AnalyticsModule],
  controllers: [AdminController, AdminSetupController],
  providers: [AdminService],
})
export class AdminModule {}
