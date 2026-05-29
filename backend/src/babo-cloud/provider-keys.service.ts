import { Injectable, BadRequestException } from '@nestjs/common';
import { SettingsService } from '../settings/settings.service';
import { CryptoService } from './crypto.service';
import {
  CloudUpstreamService,
  InferenceProviderId,
  PROVIDER_OPENAI_BASE_URLS,
} from './cloud-upstream.service';

export interface ResolvedInferenceUpstream {
  baseUrl: string;
  apiKey: string;
  placement: string;
  provider: string;
}

export interface ResendConfig {
  apiKey: string;
  inboundDomain: string;
}

@Injectable()
export class ProviderKeysService {
  constructor(
    private settings: SettingsService,
    private crypto: CryptoService,
    private upstream: CloudUpstreamService,
  ) {}

  async setInferenceProviderKey(
    userId: string,
    provider: string,
    apiKey: string,
  ): Promise<void> {
    if (!PROVIDER_OPENAI_BASE_URLS[provider]) {
      throw new BadRequestException(`Unsupported provider: ${provider}`);
    }
    const data = await this.settings.getSettings(userId);
    const keys = { ...(data.provider_keys_encrypted as Record<string, string>) };
    keys[`inference:${provider}`] = this.crypto.encrypt(apiKey.trim());
    await this.settings.updateSettings(userId, {
      ...data,
      provider_keys_encrypted: keys,
      cloud_inference_mode: 'byok',
      cloud_inference_provider: provider,
    });
  }

  async clearInferenceProviderKey(
    userId: string,
    provider: string,
  ): Promise<void> {
    const data = await this.settings.getSettings(userId);
    const keys = { ...(data.provider_keys_encrypted as Record<string, string>) };
    delete keys[`inference:${provider}`];
    const nextMode = this.upstream.isResoldInferenceConfigured()
      ? 'resold'
      : 'byok';
    await this.settings.updateSettings(userId, {
      ...data,
      provider_keys_encrypted: keys,
      cloud_inference_mode: nextMode,
    });
  }

  async setResendConfig(
    userId: string,
    apiKey: string,
    inboundDomain: string,
  ): Promise<void> {
    const data = await this.settings.getSettings(userId);
    const keys = { ...(data.provider_keys_encrypted as Record<string, string>) };
    keys['resend:api'] = this.crypto.encrypt(apiKey.trim());
    keys['resend:domain'] = this.crypto.encrypt(inboundDomain.trim());
    await this.settings.updateSettings(userId, {
      ...data,
      provider_keys_encrypted: keys,
      email_mode: 'byo_resend',
    });
  }

  async getResendConfig(userId: string): Promise<ResendConfig | null> {
    const data = await this.settings.getSettings(userId);
    const keys = data.provider_keys_encrypted as Record<string, string> | undefined;
    if (!keys?.['resend:api'] || !keys?.['resend:domain']) return null;
    try {
      return {
        apiKey: this.crypto.decrypt(keys['resend:api']),
        inboundDomain: this.crypto.decrypt(keys['resend:domain']),
      };
    } catch {
      return null;
    }
  }

  async getResendStatus(userId: string): Promise<{
    configured: boolean;
    source: 'byo' | null;
    inboundDomain: string | null;
  }> {
    const byo = await this.getResendConfig(userId);
    if (!byo) {
      return { configured: false, source: null, inboundDomain: null };
    }
    return {
      configured: true,
      source: 'byo',
      inboundDomain: byo.inboundDomain,
    };
  }

  async clearResendConfig(userId: string): Promise<void> {
    const data = await this.settings.getSettings(userId);
    const keys = { ...(data.provider_keys_encrypted as Record<string, string>) };
    delete keys['resend:api'];
    delete keys['resend:domain'];
    await this.settings.updateSettings(userId, {
      ...data,
      provider_keys_encrypted: keys,
      email_mode: '',
    });
  }

  async resolveInferenceUpstream(
    userId: string,
  ): Promise<ResolvedInferenceUpstream> {
    const data = await this.settings.getSettings(userId);
    const mode = (data.cloud_inference_mode as string) || '';
    const provider =
      (data.cloud_inference_provider as string) || 'openrouter';
    const keys =
      (data.provider_keys_encrypted as Record<string, string>) || {};

    if (mode === 'hosted') {
      if (!this.upstream.isInferenceConfigured()) {
        throw new BadRequestException('Hosted inference is not configured');
      }
      return {
        baseUrl: this.upstream.inferenceApiBase(),
        apiKey: this.upstream.inferenceUpstreamKey,
        placement: 'hosted_babo',
        provider: 'babo',
      };
    }

    const userEnc = keys[`inference:${provider}`];
    if (userEnc && (mode === 'byok' || !mode)) {
      const base = PROVIDER_OPENAI_BASE_URLS[provider];
      if (!base) {
        throw new BadRequestException(`Unknown provider: ${provider}`);
      }
      return {
        baseUrl: base,
        apiKey: this.crypto.decrypt(userEnc),
        placement: 'byok_cloud',
        provider,
      };
    }

    if (
      this.upstream.isResoldInferenceConfigured() &&
      (mode === 'resold' || mode === '' || !userEnc)
    ) {
      return {
        baseUrl: PROVIDER_OPENAI_BASE_URLS.openrouter,
        apiKey: this.upstream.platformOpenRouterKey,
        placement: 'babo_resold',
        provider: 'openrouter',
      };
    }

    if (mode === 'byok') {
      throw new BadRequestException(
        `No API key stored for provider ${provider}. Add one in Settings or use Babo Cloud trial.`,
      );
    }

    throw new BadRequestException(
      'Inference is not configured. Set PLATFORM_OPENROUTER_API_KEY on the server or add your provider API key.',
    );
  }

  async listConfiguredProviders(userId: string): Promise<string[]> {
    const d = await this.settings.getSettings(userId);
    const keys = (d.provider_keys_encrypted as Record<string, string>) || {};
    return Object.keys(keys)
      .filter((k) => k.startsWith('inference:'))
      .map((k) => k.replace('inference:', ''));
  }
}
