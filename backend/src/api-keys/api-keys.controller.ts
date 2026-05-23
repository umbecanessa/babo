import { Controller, Get, Post, Delete, Body, Param, UseGuards, Request } from '@nestjs/common';
import { JwtAuthGuard } from '../auth/jwt-auth.guard';
import { ApiKeysService } from './api-keys.service';
import { CreateApiKeyDto } from './dto';

@Controller('api-keys')
@UseGuards(JwtAuthGuard)
export class ApiKeysController {
  constructor(private apiKeys: ApiKeysService) {}

  @Post()
  create(@Request() req: any, @Body() dto: CreateApiKeyDto) {
    return this.apiKeys.create(req.user.userId, dto.name, dto.rateLimitRpm);
  }

  @Get()
  findAll(@Request() req: any) {
    return this.apiKeys.findAll(req.user.userId);
  }

  @Delete(':id')
  remove(@Request() req: any, @Param('id') id: string) {
    return this.apiKeys.delete(req.user.userId, id);
  }

  @Post(':id/revoke')
  revoke(@Request() req: any, @Param('id') id: string) {
    return this.apiKeys.revoke(req.user.userId, id);
  }
}
