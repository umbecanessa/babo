import { Module } from '@nestjs/common';
import { AuthModule } from '../auth/auth.module';
import { AnalyticsController } from './analytics.controller';
import { AnalyticsService } from './analytics.service';
import { OptionalJwtAuthGuard } from './optional-jwt-auth.guard';

@Module({
  imports: [AuthModule],
  controllers: [AnalyticsController],
  providers: [AnalyticsService, OptionalJwtAuthGuard],
  exports: [AnalyticsService],
})
export class AnalyticsModule {}
