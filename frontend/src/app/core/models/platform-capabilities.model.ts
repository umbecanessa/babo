export interface PlatformCapabilities {
  baboCloudMode: boolean;
  email: {
    available: boolean;
    source: 'babo_server' | 'byo' | 'server_env' | null;
    byoConfigured: boolean;
    serverConfigured: boolean;
    inboundDomain: string | null;
    inboundWebhookPath: string;
  };
  google: {
    baboOAuthAvailable: boolean;
    requiresByoCredentials: boolean;
  };
  telegram: {
    mode: 'self_service';
  };
  whatsapp: {
    mode: 'local_baileys';
  };
  relay: {
    requiredForWebhooks: boolean;
    webhookPathPattern: string;
  };
  publicApiBase: string | null;
}

export interface ResendProviderStatus {
  configured: boolean;
  inboundDomain: string | null;
}
