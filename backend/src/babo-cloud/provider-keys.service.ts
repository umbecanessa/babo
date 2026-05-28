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
    await this.settings.updateSettings(userId, {
      ...data,
      provider_keys_encrypted: keys,
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

  async resolveInferenceUpstream(
    userId: string,
  ): Promise<ResolvedInferenceUpstream> {
    const data = await this.settings.getSettings(userId);
    const mode = (data.cloud_inference_mode as string) || 'hosted';

    if (mode === 'byok') {
      const provider =
        (data.cloud_inference_provider as string) || 'openrouter';
      const base = PROVIDER_OPENAI_BASE_URLS[provider];
      if (!base) {
        throw new BadRequestException(`Unknown provider: ${provider}`);
      }
      const keys = data.provider_keys_encrypted as Record<string, string>;
      const enc = keys?.[`inference:${provider}`];
      if (!enc) {
        throw new BadRequestException(
          `No API key stored for provider ${provider}`,
        );
      }
      return {
        baseUrl: base,
        apiKey: this.crypto.decrypt(enc),
        placement: 'byok_cloud',
        provider,
      };
    }

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

  async listConfiguredProviders(userId: string): Promise<string[]> {
    const d = await this.settings.getSettings(userId);
    const keys = (d.provider_keys_encrypted as Record<string, string>) || {};
    return Object.keys(keys)
      .filter((k) => k.startsWith('inference:'))
      .map((k) => k.replace('inference:', ''));
  }
}
