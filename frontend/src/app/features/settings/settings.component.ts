/**
 * NLS Settings -- Global configuration page.
 *
 * Sections:
 *   - Model Provider (Local / Cloud toggle)
 *   - Connection (runtime server URL, status)
 *   - Channels (quick-action cards with onboarding flows)
 *   - API Keys (links to existing page)
 *   - Appearance (theme, font)
 */

import { Component, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { HttpClient } from '@angular/common/http';
import { ToastService } from '../../shared/toast/toast.service';
import { ApiKeyService } from '../../core/services/api-key.service';
import { ApiService } from '../../core/services/api.service';
import { OnboardingService } from '../../shared/onboarding/onboarding.service';
import { GoogleConnectModalComponent } from '../../shared/google-connect-modal/google-connect-modal.component';
import { ApiKey } from '../../core/models/user.model';
import { environment } from '../../../environments/environment';

interface NlsSettings {
  model_provider: 'local' | 'cloud';
  // Local
  model_path: string;
  gpu_layers: number;
  context_size: number;
  // Cloud -- Babo Cloud only (API key auto-managed)
  cloud_api_key_id: string;
  // Connection
  runtime_url: string;
  // Agent defaults
  default_workspace: string;
  // Appearance
  editor_font_size: number;
  editor_font_family: string;
  theme: 'dark' | 'light';
}

@Component({
  selector: 'app-settings',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, GoogleConnectModalComponent],
  templateUrl: './settings.component.html',
  styleUrl: './settings.component.scss',
})
export class SettingsComponent implements OnInit {
  private readonly API = environment.apiUrl;

  settings = signal<NlsSettings>({
    model_provider: 'local',
    model_path: '',
    gpu_layers: -1,
    context_size: 8192,
    cloud_api_key_id: '',
    runtime_url: 'http://localhost:8443',
    default_workspace: '',
    editor_font_size: 13,
    editor_font_family: "'SF Mono', 'Fira Code', monospace",
    theme: 'dark',
  });

  activeSection = signal<string>('model');
  connectionStatus = signal<'connected' | 'disconnected' | 'checking'>('checking');
  saving = signal(false);

  /** Cloud API key state */
  cloudApiKey = signal<ApiKey | null>(null);
  cloudKeyLoading = signal(false);
  cloudKeyGenerating = signal(false);

  /** Channel status */
  channelStatus = signal<Record<string, any>>({
    telegram: { connected: false, enabled: false },
    email: { connected: false, enabled: false },
    whatsapp: { connected: false, enabled: false },
    google_workspace: { connected: false, enabled: false, email: '' },
  });
  channelsLoading = signal(false);

  sections = [
    { id: 'model', label: 'Model Provider', icon: 'cpu' },
    { id: 'connection', label: 'Connection', icon: 'link' },
    { id: 'channels', label: 'Channels', icon: 'broadcast' },
    { id: 'keys', label: 'API Keys', icon: 'key' },
    { id: 'defaults', label: 'Agent Defaults', icon: 'sliders' },
    { id: 'appearance', label: 'Appearance', icon: 'palette' },
    { id: 'general', label: 'General', icon: 'settings' },
  ];

  /** Google Workspace connect modal */
  googleModalOpen = signal(false);

  /** Email alias shown after activation */
  emailAlias = signal<string>('');
  emailActivating = signal(false);

  /** First agent ID for channel operations */
  agentId = '';

  constructor(
    private http: HttpClient,
    private toast: ToastService,
    private apiKeyService: ApiKeyService,
    private apiService: ApiService,
    private onboardingService: OnboardingService,
    private router: Router,
  ) {}

  resetOnboarding(): void {
    this.onboardingService.resetAll();
    this.toast.show('Onboarding tutorials reset. They will appear again on each page.', 'info', 3000);
  }

  ngOnInit(): void {
    this.loadSettings();
    this.checkConnection();
    this.loadAgentId();
    this.loadChannelStatus();
  }

  private loadAgentId(): void {
    this.apiService.getAgents().subscribe({
      next: (agents) => {
        if (agents.length > 0) {
          this.agentId = agents[0].runtimeAgentId || agents[0].id;
        }
      },
    });
  }

  loadSettings(): void {
    this.http.get<NlsSettings>(`${this.API}/settings`).subscribe({
      next: (s) => {
        this.settings.set({ ...this.settings(), ...s });
        // If cloud mode, fetch the API key
        if (s.model_provider === 'cloud') {
          this.fetchOrCreateCloudKey();
        }
      },
      error: () => {
        // Settings endpoint may not exist yet -- use defaults
      },
    });
  }

  saveSettings(): void {
    this.saving.set(true);
    this.http.put(`${this.API}/settings`, this.settings()).subscribe({
      next: () => {
        this.saving.set(false);
        this.toast.show('Settings saved', 'info', 2000);
      },
      error: (err) => {
        this.saving.set(false);
        this.toast.show('Failed to save: ' + (err.error?.message || err.message), 'error');
      },
    });
  }

  checkConnection(): void {
    this.connectionStatus.set('checking');
    this.http.get(`${this.API}/health`).subscribe({
      next: () => this.connectionStatus.set('connected'),
      error: () => this.connectionStatus.set('disconnected'),
    });
  }

  updateSetting(key: keyof NlsSettings, value: any): void {
    this.settings.update((s) => ({ ...s, [key]: value }));

    // When switching to cloud, auto-fetch or create the API key
    if (key === 'model_provider' && value === 'cloud') {
      this.fetchOrCreateCloudKey();
    }
  }

  /**
   * Fetch the user's active NLS API key.
   * If none exists, auto-generate one so the cloud mode is ready immediately.
   */
  fetchOrCreateCloudKey(): void {
    this.cloudKeyLoading.set(true);
    this.apiKeyService.getKeys().subscribe({
      next: (keys) => {
        // Find an active key
        const active = keys.find((k) => k.isActive);
        if (active) {
          this.cloudApiKey.set(active);
          this.settings.update((s) => ({ ...s, cloud_api_key_id: active.id }));
          this.cloudKeyLoading.set(false);
        } else {
          // No active key -- auto-generate one
          this.generateCloudKey();
        }
      },
      error: () => {
        this.cloudKeyLoading.set(false);
        this.toast.show('Failed to retrieve API keys', 'error');
      },
    });
  }

  /** Generate a new NLS API key for cloud mode. */
  generateCloudKey(): void {
    this.cloudKeyGenerating.set(true);
    this.cloudKeyLoading.set(true);
    this.apiKeyService.createKey('Babo Cloud (auto)').subscribe({
      next: (key) => {
        this.cloudApiKey.set(key);
        this.settings.update((s) => ({ ...s, cloud_api_key_id: key.id }));
        this.cloudKeyGenerating.set(false);
        this.cloudKeyLoading.set(false);
        this.toast.show('API key generated automatically', 'info', 3000);
      },
      error: () => {
        this.cloudKeyGenerating.set(false);
        this.cloudKeyLoading.set(false);
        this.toast.show('Failed to generate API key', 'error');
      },
    });
  }

  // -- Channel management --------------------------------------------------

  loadChannelStatus(): void {
    this.channelsLoading.set(true);
    const runtimeUrl = this.apiService.runtimeBase;
    const apiUrl = this.apiService.apiBase;
    const agentId = this.agentId || 'default';
    let loaded = 0;
    const total = 3;

    // Email status goes through NestJS
    this.http.get(`${apiUrl}/channels/email/status/${agentId}`).subscribe({
      next: (status: any) => {
        this.channelStatus.update((s) => ({ ...s, email: status }));
        if (status?.alias) this.emailAlias.set(status.alias);
        loaded++;
        if (loaded >= total) this.channelsLoading.set(false);
      },
      error: () => { loaded++; if (loaded >= total) this.channelsLoading.set(false); },
    });

    // Telegram and WhatsApp status from Python runtime
    for (const ch of ['telegram', 'whatsapp']) {
      this.http.get(`${runtimeUrl}/skills/${ch}-channel/status/${agentId}`).subscribe({
        next: (status: any) => {
          this.channelStatus.update((s) => ({ ...s, [ch]: status }));
          loaded++;
          if (loaded >= total) this.channelsLoading.set(false);
        },
        error: () => { loaded++; if (loaded >= total) this.channelsLoading.set(false); },
      });
    }

    // Google Workspace status from Python runtime
    this.http.get(`${runtimeUrl}/skills/google-workspace/status/${agentId}`).subscribe({
      next: (status: any) => {
        this.channelStatus.update((s) => ({ ...s, google_workspace: status }));
      },
      error: () => {},
    });
  }

  connectEmail(): void {
    if (!this.agentId) {
      this.toast.show('No agent found. Create one first.', 'error');
      return;
    }
    this.emailActivating.set(true);
    this.http.post<any>(`${this.apiService.apiBase}/channels/email/activate/${this.agentId}`, {}).subscribe({
      next: (res) => {
        this.emailActivating.set(false);
        this.emailAlias.set(res.alias || '');
        this.channelStatus.update((s) => ({
          ...s,
          email: { connected: true, enabled: true, alias: res.alias, from_address: res.from_address },
        }));
        this.toast.show('Email channel activated!', 'info', 3000);
      },
      error: (err) => {
        this.emailActivating.set(false);
        const detail = err.error?.detail || err.message || 'Failed to activate email';
        this.toast.show(detail, 'error', 5000);
      },
    });
  }

  connectTelegram(): void {
    if (!this.agentId) {
      this.toast.show('No agent found. Create one first.', 'error');
      return;
    }
    this.router.navigate(['/chat', this.agentId], {
      queryParams: { setup: 'telegram-channel' },
    });
  }

  connectWhatsApp(): void {
    this.toast.show('WhatsApp integration is coming soon!', 'info', 3000);
  }

  connectGoogleWorkspace(): void {
    if (!this.agentId) {
      this.toast.show('No agent found. Create one first.', 'error');
      return;
    }
    this.googleModalOpen.set(true);
  }

  onGoogleConnected(event: { email: string }): void {
    this.channelStatus.update((s) => ({
      ...s,
      google_workspace: { connected: true, enabled: true, email: event.email },
    }));
  }

  closeGoogleModal(): void {
    this.googleModalOpen.set(false);
    this.loadChannelStatus();
  }

  copyAlias(): void {
    const alias = this.emailAlias() || this.getChannelStatus('email').alias || '';
    if (!alias) return;
    navigator.clipboard.writeText(alias).then(() => {
      this.toast.show('Email address copied!', 'info', 2000);
    });
  }

  getChannelStatus(channel: string): any {
    return this.channelStatus()[channel] || { connected: false, enabled: false };
  }
}
