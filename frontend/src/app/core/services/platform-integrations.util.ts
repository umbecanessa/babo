import type { BackendChoiceId } from '../../features/setup/setup-backend.util';
import { normalizeNestjsUrl } from '../../features/setup/setup-backend.util';
import type { PlatformCapabilities } from '../models/platform-capabilities.model';

export type IntegrationChannelId = 'email' | 'telegram' | 'whatsapp' | 'google-workspace' | 'discord' | 'slack';

export interface IntegrationSetupContext {
  channel: IntegrationChannelId;
  usesBaboCloudBackend: boolean;
  ready: boolean;
  blockedReason?: string;
  credentialMode: 'babo' | 'byo' | 'self_service' | 'local';
  setupSteps: string[];
  settingsPath?: string;
}

export function usesBaboCloudBackend(choice: BackendChoiceId): boolean {
  return choice === 'babo_cloud';
}

/** NestJS on this computer (localhost) — webhook channels need a public URL elsewhere. */
export function isLocalNestBackend(choice: BackendChoiceId): boolean {
  return choice === 'local';
}

export function localNestWebhookWarning(
  backendChoice: BackendChoiceId,
  nestjsUrl: string,
): string | null {
  if (!isLocalNestBackend(backendChoice)) return null;
  const host = normalizeNestjsUrl(nestjsUrl) || 'http://localhost:3000';
  return (
    `NestJS is on ${host} — Resend and Telegram cannot call localhost. ` +
    'Deploy NestJS to a public HTTPS URL (Railway, VPS, homelab) or use a tunnel, ' +
    'set PUBLIC_API_URL on the server, and point webhooks at that public base URL.'
  );
}

export function emailIsReady(
  caps: PlatformCapabilities | null,
  backendChoice: BackendChoiceId,
): boolean {
  if (caps?.email.available) return true;
  // Capabilities fetch can fail offline; don't block Babo Cloud one-click activate.
  return usesBaboCloudBackend(backendChoice);
}

export function googleUsesByo(choice: BackendChoiceId): boolean {
  return !usesBaboCloudBackend(choice);
}

export function nestWebhookBase(
  nestjsUrl: string,
  caps: PlatformCapabilities | null,
): string {
  if (caps?.publicApiBase) return caps.publicApiBase.replace(/\/+$/, '');
  return nestjsUrl.replace(/\/+$/, '');
}

export function buildIntegrationContext(
  channel: IntegrationChannelId,
  backendChoice: BackendChoiceId,
  caps: PlatformCapabilities | null,
  nestjsUrl: string,
): IntegrationSetupContext {
  const baboBackend = usesBaboCloudBackend(backendChoice);
  const webhookBase = nestWebhookBase(nestjsUrl, caps);

  switch (channel) {
    case 'email': {
      const ready = emailIsReady(caps, backendChoice);
      const byo = caps?.email.byoConfigured;
      const server = caps?.email.serverConfigured;
      const steps: string[] = [];
      if (!baboBackend) {
        const localWarn = localNestWebhookWarning(backendChoice, nestjsUrl);
        if (localWarn) {
          steps.push(localWarn);
          steps.push(
            'Until NestJS has a public URL, email and Telegram inbound will not work — WhatsApp QR pairing still works locally.',
          );
        } else {
          steps.push(
            'Deploy NestJS with a public HTTPS URL (Railway, VPS, etc.) so Resend can reach your webhook.',
          );
        }
        steps.push(
          'Create a Resend account, verify an inbound domain, and obtain an API key.',
          `Point Resend inbound webhook to: ${webhookBase}/api/channels/email/webhook`,
        );
        if (!server) {
          steps.push(
            'Either set RESEND_API_KEY + RESEND_INBOUND_DOMAIN on your NestJS server,',
            'or save your Resend credentials under Settings → Integrations.',
          );
        }
        steps.push(
          'Keep Babo Desktop running so the NestJS relay can deliver inbound mail to your agent.',
        );
      } else if (!ready) {
        steps.push('Email is not available on this Babo Cloud server yet — contact support.');
      } else {
        steps.push('Click Activate Email — Babo provisions an @inbox address for your agent.');
      }
      return {
        channel,
        usesBaboCloudBackend: baboBackend,
        ready,
        blockedReason: ready
          ? undefined
          : 'Configure Resend before activating the email channel.',
        credentialMode: baboBackend && server && !byo ? 'babo' : 'byo',
        setupSteps: steps,
        settingsPath: '/settings',
      };
    }

    case 'google-workspace': {
      const byo = googleUsesByo(backendChoice);
      const steps = byo
        ? [
            'Create a Google Cloud project and enable Gmail, Calendar, Drive, and Sheets APIs.',
            'Create OAuth 2.0 credentials (Desktop app or Web application).',
            'Add redirect URI: http://localhost:9222/skills/google-workspace/oauth/callback',
            'In Tools → Google Workspace, save client_id and client_secret, then Connect.',
            'Or ask the agent in chat to help you through Google Cloud setup.',
          ]
        : [
            'Click Connect — Babo opens Google sign-in using the built-in OAuth app.',
            'Grant the requested scopes for Gmail, Calendar, Drive, and Sheets.',
          ];
      return {
        channel,
        usesBaboCloudBackend: baboBackend,
        ready: true,
        credentialMode: byo ? 'byo' : 'babo',
        setupSteps: steps,
      };
    }

    case 'telegram': {
      const steps = [
        'Open Telegram and message @BotFather — send /newbot and follow the prompts.',
        'Copy the bot token BotFather gives you.',
        baboBackend
          ? 'Use Setup in Chat — the agent validates the token and registers the webhook on Babo Cloud.'
          : isLocalNestBackend(backendChoice)
            ? 'After NestJS has a public HTTPS URL, use Setup in Chat — the agent registers the Telegram webhook on that URL (not localhost).'
            : `Ensure your NestJS server is public (${webhookBase}) and Babo Desktop is online (relay).`,
        'Paste the bot token when prompted, then finish owner identity and DM policy in config.',
      ];
      return {
        channel,
        usesBaboCloudBackend: baboBackend,
        ready: true,
        credentialMode: 'self_service',
        setupSteps: steps,
      };
    }

    case 'whatsapp': {
      const steps = [
        'WhatsApp uses a local Baileys bridge on your desktop — no NestJS webhook needed for inbound.',
        'Click Start Pairing and scan the QR code with WhatsApp → Linked devices.',
        'Set owner phone number and DM policy after pairing.',
        'Keep Babo Desktop running while you expect the agent to receive WhatsApp messages.',
      ];
      return {
        channel,
        usesBaboCloudBackend: baboBackend,
        ready: true,
        credentialMode: 'local',
        setupSteps: steps,
      };
    }

    case 'discord': {
      const steps = [
        'Create a bot in the Discord Developer Portal and copy the bot token.',
        baboBackend
          ? 'Setup in Chat — Babo Cloud runs the Discord Gateway on NestJS and relays messages to your desktop.'
          : `Ensure NestJS is public (${webhookBase}) and Babo Desktop relay is online.`,
        'Invite the bot to your server and channels in Discord — scope syncs back to Babo automatically.',
        'Configure owner identity, DM policy, and channel scope in the form below.',
      ];
      return {
        channel,
        usesBaboCloudBackend: baboBackend,
        ready: true,
        credentialMode: 'self_service',
        setupSteps: steps,
      };
    }

    case 'slack': {
      const requestUrl = `${webhookBase}/api/channels/webhook/slack/{your-agent-id}`;
      const steps = [
        'Create a Slack app at api.slack.com/apps with bot scopes (app_mentions:read, chat:write, im:history, channels:history).',
        'Enable Event Subscriptions and set Request URL to:',
        requestUrl,
        'Copy the bot token (xoxb-…) and signing secret into Setup in Chat or the config form below.',
        'Invite the app to channels in Slack — membership syncs back to Babo.',
      ];
      return {
        channel,
        usesBaboCloudBackend: baboBackend,
        ready: true,
        credentialMode: 'self_service',
        setupSteps: steps,
      };
    }
  }
}

export function selfHostedPrerequisiteSteps(
  backendChoice: BackendChoiceId,
  nestjsUrl: string,
  caps: PlatformCapabilities | null,
): string[] {
  if (usesBaboCloudBackend(backendChoice)) return [];

  const webhookBase = nestWebhookBase(nestjsUrl, caps);
  const localWarn = localNestWebhookWarning(backendChoice, nestjsUrl);

  if (localWarn) {
    return [
      localWarn,
      'Set PUBLIC_API_URL on your NestJS deployment to the public HTTPS origin so Settings and Tools show the correct webhook URLs.',
      'Babo Desktop must be running — NestJS forwards webhooks to your local agent over the relay WebSocket.',
      'Email and Google use Babo-provided credentials on Babo Cloud; on your own server you configure Resend and Google OAuth yourself.',
      'WhatsApp pairs locally via QR — it does not use the NestJS webhook relay for inbound messages.',
    ];
  }

  return [
    `Your NestJS server (${webhookBase}) must be reachable over HTTPS for Telegram and email webhooks.`,
    'Babo Desktop must be running — NestJS forwards webhooks to your local agent over the relay WebSocket.',
    'Email and Google use Babo-provided credentials on Babo Cloud; on your own server you configure Resend and Google OAuth yourself.',
    'WhatsApp pairs locally via QR — it does not use the NestJS webhook relay for inbound messages.',
  ];
}
