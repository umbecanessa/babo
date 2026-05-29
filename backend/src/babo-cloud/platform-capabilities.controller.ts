import { Controller, Get, Req, UseGuards } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { JwtAuthGuard } from '../auth/jwt-auth.guard';
import { CloudUpstreamService } from './cloud-upstream.service';
import { ProviderKeysService } from './provider-keys.service';

@Controller('cloud')
@UseGuards(JwtAuthGuard)
export class PlatformCapabilitiesController {
  constructor(
    private config: ConfigService,
    private upstream: CloudUpstreamService,
    private providerKeys: ProviderKeysService,
  ) {}

  /**
   * Reports which platform integrations are configured on this NestJS instance
   * and for the current user. The desktop app combines this with backend choice
   * (Babo Cloud vs self-hosted) to drive integration setup UX.
   */
  @Get('platform-capabilities')
  async getPlatformCapabilities(@Req() req: any) {
    const userId = req.user.userId;
    const resend = await this.providerKeys.getResendStatus(userId);

    const serverResendKey = !!this.config.get<string>('RESEND_API_KEY');
    const serverResendDomain =
      this.config.get<string>('RESEND_INBOUND_DOMAIN') || '';
    const serverResendConfigured = serverResendKey && !!serverResendDomain;

    const emailAvailable = resend.configured || serverResendConfigured;
    let emailSource: 'babo_server' | 'byo' | 'server_env' | null = null;
    if (resend.source === 'byo') {
      emailSource = 'byo';
    } else if (serverResendConfigured) {
      emailSource = this.upstream.cloudMode ? 'babo_server' : 'server_env';
    }

    const webhookBase =
      this.config.get<string>('PUBLIC_API_URL') ||
      this.config.get<string>('APP_URL') ||
      '';

    return {
      baboCloudMode: this.upstream.cloudMode,
      email: {
        available: emailAvailable,
        source: emailSource,
        byoConfigured: resend.source === 'byo',
        serverConfigured: serverResendConfigured,
        inboundDomain: resend.inboundDomain || serverResendDomain || null,
        inboundWebhookPath: '/api/channels/email/webhook',
      },
      google: {
        /** Babo ships a built-in OAuth app — intended for Babo Cloud users. */
        baboOAuthAvailable: this.upstream.cloudMode,
        requiresByoCredentials: !this.upstream.cloudMode,
      },
      telegram: {
        /** User supplies BotFather token; needs public Nest webhook URL. */
        mode: 'self_service',
      },
      whatsapp: {
        /** Baileys bridge runs on the desktop; no Nest webhook ingress. */
        mode: 'local_baileys',
      },
      relay: {
        /** NestJS relay WebSocket exposes local runtime to webhook ingress. */
        requiredForWebhooks: true,
        webhookPathPattern: '/api/channels/webhook/{channel}/{runtimeAgentId}',
      },
      publicApiBase: webhookBase.replace(/\/+$/, '') || null,
    };
  }
}
