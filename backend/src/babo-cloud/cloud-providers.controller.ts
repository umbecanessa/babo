import {
  Body,
  Controller,
  Delete,
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
}
