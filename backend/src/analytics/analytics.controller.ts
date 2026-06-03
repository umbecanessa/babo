import { Body, Controller, Get, Param, Post, Req, UseGuards } from '@nestjs/common';
import { AnalyticsService } from './analytics.service';
import { ClaimHandoffDto, CreateHandoffDto } from './dto/handoff.dto';
import { TrackAnalyticsEventsDto } from './dto/track-events.dto';
import { OptionalJwtAuthGuard } from './optional-jwt-auth.guard';
@Controller('analytics')
export class AnalyticsController {
  constructor(private analytics: AnalyticsService) {}

  /** Public config so clients skip sending events when analytics is disabled. */
  @Get('config')
  getConfig() {
    return this.analytics.getPublicConfig();
  }

  /** Anonymous product events (setup funnel, etc.). No-op when BABO_ANALYTICS_ENABLED is off. */
  @Post('events')
  @UseGuards(OptionalJwtAuthGuard)
  trackEvents(@Body() dto: TrackAnalyticsEventsDto, @Req() req: any) {
    const userId = req.user?.userId ?? null;
    return this.analytics.ingestEvents(dto.events, {
      userId,
      source: 'app',
    });
  }

  /** Marketing site events (landing page UA funnel). No-op when BABO_ANALYTICS_ENABLED is off. */
  @Post('web-events')
  trackWebEvents(@Body() dto: TrackAnalyticsEventsDto) {
    return this.analytics.ingestEvents(dto.events, {
      userId: null,
      source: 'web',
    });
  }

  /** Create a short-lived ref so the desktop app can claim landing UTMs after install. */
  @Post('handoff')
  createHandoff(@Body() dto: CreateHandoffDto) {
    return this.analytics.createHandoff(dto.visitorId, dto.properties);
  }

  @Post('handoff/:ref/claim')
  claimHandoff(@Param('ref') ref: string, @Body() dto: ClaimHandoffDto) {
    return this.analytics.claimHandoff(ref, dto.installId);
  }
}
