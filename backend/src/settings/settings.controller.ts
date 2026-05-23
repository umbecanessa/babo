import { Controller, Get, Put, Body, UseGuards, Request } from '@nestjs/common';
import { JwtAuthGuard } from '../auth/jwt-auth.guard';
import { SettingsService } from './settings.service';

@Controller('settings')
@UseGuards(JwtAuthGuard)
export class SettingsController {
  constructor(private settings: SettingsService) {}

  @Get()
  getSettings(@Request() req: any) {
    return this.settings.getSettings(req.user.userId);
  }

  @Put()
  updateSettings(@Request() req: any, @Body() body: Record<string, any>) {
    return this.settings.updateSettings(req.user.userId, body);
  }
}
