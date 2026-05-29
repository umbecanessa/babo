import {
  Body,
  Controller,
  Delete,
  Get,
  Param,
  Put,
  Req,
  UseGuards,
} from '@nestjs/common';
import { JwtAuthGuard } from '../auth/jwt-auth.guard';
import { ProviderKeysService } from './provider-keys.service';

@Controller('cloud/providers')
@UseGuards(JwtAuthGuard)
export class CloudProvidersController {
  constructor(private providerKeys: ProviderKeysService) {}

  @Put('inference/:provider')
  async setInferenceKey(
    @Req() req: any,
    @Param('provider') provider: string,
    @Body() body: { apiKey: string },
  ) {
    await this.providerKeys.setInferenceProviderKey(
      req.user.userId,
      provider,
      body.apiKey,
    );
    return { ok: true, provider };
  }

  @Delete('inference/:provider')
  async clearInferenceKey(
    @Req() req: any,
    @Param('provider') provider: string,
  ) {
    await this.providerKeys.clearInferenceProviderKey(
      req.user.userId,
      provider,
    );
    return { ok: true };
  }

  @Get('resend')
  async getResend(@Req() req: any) {
    const status = await this.providerKeys.getResendStatus(req.user.userId);
    return {
      configured: status.configured,
      inboundDomain: status.inboundDomain,
    };
  }

  @Put('resend')
  async setResend(
    @Req() req: any,
    @Body() body: { apiKey: string; inboundDomain: string },
  ) {
    await this.providerKeys.setResendConfig(
      req.user.userId,
      body.apiKey,
      body.inboundDomain,
    );
    return { ok: true };
  }

  @Delete('resend')
  async clearResend(@Req() req: any) {
    await this.providerKeys.clearResendConfig(req.user.userId);
    return { ok: true };
  }
}
