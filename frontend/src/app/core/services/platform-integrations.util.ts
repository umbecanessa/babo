import type { BackendChoiceId } from '../../features/setup/setup-backend.util';
import { normalizeNestjsUrl } from '../../features/setup/setup-backend.util';
import type { PlatformCapabilities } from '../models/platform-capabilities.model';

export type IntegrationChannelId = 'email' | 'telegram' | 'whatsapp' | 'google-workspace' | 'discord' | 'slack';

export interface IntegrationSetupStep {
  key: string;
  params?: Record<string, string>;
}

export interface IntegrationSetupContext {
  channel: IntegrationChannelId;
  usesBaboCloudBackend: boolean;
  ready: boolean;
  blockedReason?: IntegrationSetupStep;
  credentialMode: 'babo' | 'byo' | 'self_service' | 'local';
  setupSteps: IntegrationSetupStep[];
  settingsPath?: string;
}

function step(key: string, params?: Record<string, string>): IntegrationSetupStep {
  return params ? { key, params } : { key };
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
): IntegrationSetupStep | null {
  if (!isLocalNestBackend(backendChoice)) return null;
  const host = normalizeNestjsUrl(nestjsUrl) || 'http://localhost:3000';
  return step('tools.setup.common.localNestWarn', { host });
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
      const steps: IntegrationSetupStep[] = [];
      if (!baboBackend) {
        const localWarn = localNestWebhookWarning(backendChoice, nestjsUrl);
        if (localWarn) {
          steps.push(localWarn);
          steps.push(step('tools.setup.email.localInboundLimited'));
        } else {
          steps.push(step('tools.setup.email.deployPublic'));
        }
        steps.push(
          step('tools.setup.email.resendAccount'),
          step('tools.setup.email.resendWebhook', {
            url: `${webhookBase}/api/channels/email/webhook`,
          }),
        );
        if (!server) {
          steps.push(
            step('tools.setup.email.resendCredentialsServer'),
            step('tools.setup.email.resendCredentialsSettings'),
          );
        }
        steps.push(step('tools.setup.common.desktopRelay'));
      } else if (!ready) {
        steps.push(step('tools.setup.email.unavailable'));
      } else {
        steps.push(step('tools.setup.email.activate'));
      }
      return {
        channel,
        usesBaboCloudBackend: baboBackend,
        ready,
        blockedReason: ready ? undefined : step('tools.setup.email.blockedResend'),
        credentialMode: baboBackend && server && !byo ? 'babo' : 'byo',
        setupSteps: steps,
        settingsPath: '/settings',
      };
    }

    case 'google-workspace': {
      const byo = googleUsesByo(backendChoice);
      const steps = byo
        ? [
            step('tools.setup.google.byo1'),
            step('tools.setup.google.byo2'),
            step('tools.setup.google.byo3'),
            step('tools.setup.google.byo4'),
            step('tools.setup.google.byo5'),
          ]
        : [
            step('tools.setup.google.cloud1'),
            step('tools.setup.google.cloud2'),
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
        step('tools.setup.telegram.step1'),
        step('tools.setup.telegram.step2'),
        baboBackend
          ? step('tools.setup.telegram.step3Babo')
          : isLocalNestBackend(backendChoice)
            ? step('tools.setup.telegram.step3Local')
            : step('tools.setup.telegram.step3Self', { url: webhookBase }),
        step('tools.setup.telegram.step4'),
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
        step('tools.setup.whatsapp.step1'),
        step('tools.setup.whatsapp.step2'),
        step('tools.setup.whatsapp.step3'),
        step('tools.setup.whatsapp.step4'),
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
        step('tools.setup.discord.step1'),
        baboBackend
          ? step('tools.setup.discord.step2Babo')
          : step('tools.setup.discord.step2Self', { url: webhookBase }),
        step('tools.setup.discord.step3'),
        step('tools.setup.discord.step4'),
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
        step('tools.setup.slack.step1'),
        step('tools.setup.slack.step2'),
        step('tools.setup.slack.step3', { url: requestUrl }),
        step('tools.setup.slack.step4'),
        step('tools.setup.slack.step5'),
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
): IntegrationSetupStep[] {
  if (usesBaboCloudBackend(backendChoice)) return [];

  const webhookBase = nestWebhookBase(nestjsUrl, caps);
  const localWarn = localNestWebhookWarning(backendChoice, nestjsUrl);

  if (localWarn) {
    return [
      localWarn,
      step('tools.setup.selfHosted.publicApiUrl'),
      step('tools.setup.selfHosted.desktopRelay'),
      step('tools.setup.selfHosted.credentials'),
      step('tools.setup.selfHosted.whatsappLocal'),
    ];
  }

  return [
    step('tools.setup.selfHosted.httpsRequired', { url: webhookBase }),
    step('tools.setup.selfHosted.desktopRelay'),
    step('tools.setup.selfHosted.credentials'),
    step('tools.setup.selfHosted.whatsappLocal'),
  ];
}
