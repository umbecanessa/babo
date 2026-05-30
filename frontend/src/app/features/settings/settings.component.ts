/**
 * Settings — app-wide preferences.
 *
 * Desktop (Electron): local runtime, capabilities, backend URL, permissions.
 * Web: appearance + API keys via NestJS user settings.
 *
 * Per-agent integrations (Telegram, email, skills) live under Tools for each agent.
 */

import { Component, OnDestroy, OnInit, computed, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { ToastService } from '../../shared/toast/toast.service';
import { Day1CoachService } from '../../shared/onboarding/day1-coach.service';
import { PlatformService } from '../../core/services/platform.service';
import { ThemeService, ThemeMode } from '../../core/services/theme.service';
import { CapabilitySettingsPanelComponent } from '../../shared/capability-settings-panel/capability-settings-panel.component';
import { AgentModelService } from '../../core/services/agent-model.service';
import { ApiService } from '../../core/services/api.service';
import { BillingService } from '../../core/services/billing.service';
import {
  formatUsdCents,
  includedRemainingPercent,
  type CloudSubscriptionView,
} from '../../core/models/cloud-subscription.model';
import {
  isLocalNestBackend,
  localNestWebhookWarning,
  selfHostedPrerequisiteSteps,
  usesBaboCloudBackend,
} from '../../core/services/platform-integrations.util';
import { environment } from '../../../environments/environment';
import {
  BACKEND_CHOICES,
  type BackendChoiceId,
  backendDisplayLabel,
  matchBackendChoice,
  normalizeNestjsUrl,
} from '../setup/setup-backend.util';

interface WebAppearanceSettings {
  editor_font_size: number;
  editor_font_family: string;
  theme: ThemeMode;
}

interface RuntimeStatus {
  running: boolean;
  port: number;
  pid: number | null;
  uptime: number;
  error: string | null;
}

interface PermissionProfile {
  id: string;
  name: string;
  description: string;
  grants: Record<string, boolean>;
}

@Component({
  selector: 'app-settings',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, CapabilitySettingsPanelComponent],
  templateUrl: './settings.component.html',
  styleUrl: './settings.component.scss',
})
export class SettingsComponent implements OnInit, OnDestroy {
  private readonly API = environment.apiUrl;

  activeSection = signal<string>('');
  saving = signal(false);
  appVersion = signal('');

  /** Web-only appearance persisted in NestJS */
  webSettings = signal<WebAppearanceSettings>({
    editor_font_size: 13,
    editor_font_family: "'SF Mono', 'Fira Code', monospace",
    theme: 'dark',
  });

  /** Desktop backend URL */
  nestjsUrl = signal('https://api.babo.agency');
  backendChoice = signal<BackendChoiceId>('babo_cloud');
  backendTesting = signal(false);
  backendTestResult = signal<{ ok: boolean; message: string; latency: number } | null>(null);
  backendSaving = signal(false);

  /** Self-hosted platform credentials */
  resendApiKey = signal('');
  resendInboundDomain = signal('');
  resendConfigured = signal(false);
  resendSaving = signal(false);
  platformCapsLoading = signal(false);
  emailServerConfigured = signal(false);
  emailInboundDomain = signal<string | null>(null);
  nestPublicWebhookBase = signal('');

  platformCaps = signal<import('../../core/models/platform-capabilities.model').PlatformCapabilities | null>(null);

  subscription = signal<CloudSubscriptionView | null>(null);
  subscriptionLoading = signal(false);
  billingActionLoading = signal(false);
  spendCapInput = signal(15);
  onDemandEnabled = signal(false);

  readonly backendChoices = BACKEND_CHOICES;
  readonly usesBaboCloudBackend = usesBaboCloudBackend;
  readonly isLocalNestBackend = isLocalNestBackend;

  /** Desktop runtime */
  runtimeStatus = signal<RuntimeStatus | null>(null);
  runtimeAction = signal<'idle' | 'start' | 'stop' | 'restart'>('idle');
  runtimeLogs = signal<string[]>([]);
  showRuntimeLogs = signal(false);
  runtimePort = signal(9222);

  /** Desktop Python environment */
  venvReady = signal(false);
  setupComplete = signal(false);
  envResetting = signal(false);

  /** Desktop permissions */
  permissionProfiles = signal<PermissionProfile[]>([]);
  activePermissionProfile = signal<string | null>(null);

  readonly billingEnabled = computed(
    () => !!this.platformCaps()?.billing?.enabled,
  );

  readonly includedRemaining = computed(() => {
    const sub = this.subscription();
    return sub ? includedRemainingPercent(sub) : 0;
  });

  sections = computed(() => {
    const billingSection = this.billingEnabled()
      ? [{ id: 'billing', label: 'Billing' }]
      : [];
    if (this.platform.isElectron) {
      return [
        { id: 'models', label: 'Models & AI' },
        { id: 'account', label: 'Account' },
        ...billingSection,
        { id: 'integrations', label: 'Integrations' },
        { id: 'system', label: 'System' },
        { id: 'permissions', label: 'Permissions' },
        { id: 'appearance', label: 'Appearance' },
        { id: 'general', label: 'General' },
      ];
    }
    return [
      { id: 'appearance', label: 'Appearance' },
      ...billingSection,
      { id: 'integrations', label: 'Integrations' },
      { id: 'keys', label: 'API keys' },
      { id: 'general', label: 'General' },
    ];
  });

  toolsLink = computed(() => {
    const id = this.router.url.match(/\/([a-f0-9-]{36})/i)?.[1];
    return id ? ['/tools', id] : ['/dashboard'];
  });

  localWebhookWarning = computed(() =>
    localNestWebhookWarning(this.backendChoice(), this.nestjsUrl()),
  );

  integrationPrereqSteps = computed(() =>
    selfHostedPrerequisiteSteps(
      this.backendChoice(),
      this.nestjsUrl(),
      this.platformCaps(),
    ),
  );

  private runtimePollTimer: ReturnType<typeof setInterval> | null = null;

  constructor(
    private http: HttpClient,
    private api: ApiService,
    private billing: BillingService,
    private toast: ToastService,
    private day1Coach: Day1CoachService,
    private router: Router,
    private route: ActivatedRoute,
    private agentModels: AgentModelService,
    public platform: PlatformService,
    public themeService: ThemeService,
  ) {}

  ngOnInit(): void {
    const sectionParam = this.route.snapshot.queryParamMap.get('section');
    const valid = this.sections().some((s) => s.id === sectionParam);
    const first = valid && sectionParam ? sectionParam : (this.sections()[0]?.id ?? 'appearance');
    this.activeSection.set(first);

    this.webSettings.update((s) => ({ ...s, theme: this.themeService.mode() }));

    if (this.platform.isElectron) {
      void this.loadDesktopSettings();
      this.startRuntimePolling();
    } else {
      this.initWebBackendContext();
      this.loadWebSettings();
      void this.loadPlatformIntegrations();
    }

    if (first === 'integrations' || first === 'billing') {
      void this.loadPlatformIntegrations();
    }
    if (first === 'billing') {
      void this.loadSubscription();
    }

    const billingParam = this.route.snapshot.queryParamMap.get('billing');
    if (billingParam === 'success') {
      this.toast.show('Subscription active — welcome to Babo Cloud!', 'info', 4000);
    } else if (billingParam === 'canceled') {
      this.toast.show('Checkout canceled', 'info', 3000);
    }

    void this.loadAppVersion();
  }

  ngOnDestroy(): void {
    if (this.runtimePollTimer) {
      clearInterval(this.runtimePollTimer);
    }
  }

  resetOnboarding(): void {
    this.day1Coach.schedule();
    this.toast.show('First-run tour reset. Open chat to see it again.', 'info', 3000);
  }

  openSetupWizard(): void {
    this.router.navigate(['/setup']);
  }

  /** Web app: derive backend choice from the configured NestJS API URL. */
  private initWebBackendContext(): void {
    const url = normalizeNestjsUrl(this.API.replace(/\/api$/i, ''));
    this.nestjsUrl.set(url);
    this.backendChoice.set(matchBackendChoice(url));
  }

  async loadPlatformIntegrations(): Promise<void> {
    this.platformCapsLoading.set(true);
    try {
      const [caps, resend] = await Promise.all([
        firstValueFrom(this.api.getPlatformCapabilities()).catch(() => null),
        firstValueFrom(this.api.getResendProviderStatus()).catch(() => null),
      ]);
      this.platformCaps.set(caps);
      if (caps) {
        this.emailServerConfigured.set(caps.email.serverConfigured);
        this.emailInboundDomain.set(caps.email.inboundDomain);
        const base = caps.publicApiBase || this.nestjsUrl();
        this.nestPublicWebhookBase.set(base.replace(/\/+$/, ''));
      } else {
        this.emailServerConfigured.set(false);
        this.emailInboundDomain.set(null);
        this.nestPublicWebhookBase.set(this.nestjsUrl().replace(/\/+$/, ''));
      }
      this.resendConfigured.set(!!resend?.configured);
      this.resendInboundDomain.set(resend?.inboundDomain || '');
    } finally {
      this.platformCapsLoading.set(false);
    }
  }

  async saveResendCredentials(): Promise<void> {
    const apiKey = this.resendApiKey().trim();
    const domain = this.resendInboundDomain().trim();
    if (!apiKey || !domain) {
      this.toast.show('Resend API key and inbound domain are required', 'error');
      return;
    }
    this.resendSaving.set(true);
    try {
      await firstValueFrom(this.api.saveResendProvider(apiKey, domain));
      this.resendConfigured.set(true);
      this.resendApiKey.set('');
      this.toast.show('Resend credentials saved', 'info', 2500);
      await this.loadPlatformIntegrations();
    } catch (err: any) {
      this.toast.show(err?.error?.message || 'Failed to save Resend credentials', 'error');
    } finally {
      this.resendSaving.set(false);
    }
  }

  async clearResendCredentials(): Promise<void> {
    if (!confirm('Remove saved Resend credentials?')) return;
    this.resendSaving.set(true);
    try {
      await firstValueFrom(this.api.clearResendProvider());
      this.resendConfigured.set(false);
      this.resendInboundDomain.set('');
      this.toast.show('Resend credentials removed', 'info', 2500);
      await this.loadPlatformIntegrations();
    } catch (err: any) {
      this.toast.show(err?.error?.message || 'Failed to clear credentials', 'error');
    } finally {
      this.resendSaving.set(false);
    }
  }

  onSectionChange(sectionId: string): void {
    this.activeSection.set(sectionId);
    if (sectionId === 'integrations') {
      void this.loadPlatformIntegrations();
    }
    if (sectionId === 'billing') {
      void this.loadPlatformIntegrations();
      void this.loadSubscription();
    }
  }

  async loadSubscription(): Promise<void> {
    if (!this.billingEnabled()) return;
    this.subscriptionLoading.set(true);
    try {
      const view = await this.billing.refresh();
      this.subscription.set(view);
      if (view?.monthlySpendCapCents != null) {
        this.spendCapInput.set(Math.round(view.monthlySpendCapCents / 100));
      }
      this.onDemandEnabled.set(!!view?.onDemandEnabled);
    } finally {
      this.subscriptionLoading.set(false);
    }
  }

  formatUsd(cents: number): string {
    return formatUsdCents(cents);
  }

  subscriptionStatusLabel(status: CloudSubscriptionView['status']): string {
    switch (status) {
      case 'active':
        return 'Active';
      case 'past_due':
        return 'Payment issue';
      case 'canceled':
        return 'Canceled';
      case 'lifetime_comp':
        return 'Lifetime';
      default:
        return 'Not subscribed';
    }
  }

  async subscribeToCloud(): Promise<void> {
    this.billingActionLoading.set(true);
    try {
      const returnUrl = `${window.location.origin}${window.location.pathname}?section=billing`;
      const url = await this.billing.startCheckout(returnUrl);
      if (url) {
        window.location.href = url;
      }
    } catch (err: any) {
      this.toast.show(
        err?.error?.message || 'Could not start checkout',
        'error',
      );
    } finally {
      this.billingActionLoading.set(false);
    }
  }

  async openBillingPortal(): Promise<void> {
    this.billingActionLoading.set(true);
    try {
      const returnUrl = `${window.location.origin}${window.location.pathname}?section=billing`;
      const url = await this.billing.openPortal(returnUrl);
      if (url) {
        window.location.href = url;
      }
    } catch (err: any) {
      this.toast.show(
        err?.error?.message || 'Could not open billing portal',
        'error',
      );
    } finally {
      this.billingActionLoading.set(false);
    }
  }

  async saveSpendCap(): Promise<void> {
    const dollars = this.spendCapInput();
    if (!Number.isFinite(dollars) || dollars < 0) {
      this.toast.show('Enter a valid spend cap', 'error');
      return;
    }
    this.billingActionLoading.set(true);
    try {
      await this.billing.updateSpendCap(Math.round(dollars * 100));
      await this.loadSubscription();
      this.toast.show('Spend cap updated', 'info', 2500);
    } catch (err: any) {
      this.toast.show(err?.error?.message || 'Could not update spend cap', 'error');
    } finally {
      this.billingActionLoading.set(false);
    }
  }

  async setOnDemandSetting(enabled: boolean): Promise<void> {
    if (enabled === this.onDemandEnabled()) return;
    await this.toggleOnDemand();
  }

  async toggleOnDemand(): Promise<void> {
    const next = !this.onDemandEnabled();
    this.billingActionLoading.set(true);
    try {
      await this.billing.setOnDemandEnabled(next);
      this.onDemandEnabled.set(next);
      await this.loadSubscription();
      this.toast.show(
        next ? 'On-demand usage enabled' : 'On-demand usage disabled',
        'info',
        2500,
      );
    } catch (err: any) {
      this.toast.show(err?.error?.message || 'Could not update setting', 'error');
    } finally {
      this.billingActionLoading.set(false);
    }
  }

  onCapabilitiesSaved(): void {
    void this.agentModels.refreshFromConfig();
    this.toast.show('Models and capabilities saved.', 'info', 3000);
    void this.refreshRuntimeStatus();
  }

  updateTheme(mode: ThemeMode): void {
    this.webSettings.update((s) => ({ ...s, theme: mode }));
    this.themeService.setMode(mode);
  }

  updateEditorFontSize(size: number): void {
    this.webSettings.update((s) => ({ ...s, editor_font_size: size }));
  }

  updateEditorFontFamily(family: string): void {
    this.webSettings.update((s) => ({ ...s, editor_font_family: family }));
  }

  saveWebSettings(): void {
    this.saving.set(true);
    this.http.put(`${this.API}/settings`, this.webSettings()).subscribe({
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

  selectBackendChoice(id: BackendChoiceId): void {
    this.backendChoice.set(id);
    this.backendTestResult.set(null);
    const choice = BACKEND_CHOICES.find((c) => c.id === id);
    if (id !== 'custom' && choice?.url) {
      this.nestjsUrl.set(normalizeNestjsUrl(choice.url));
    }
  }

  setCustomBackendUrl(url: string): void {
    this.nestjsUrl.set(normalizeNestjsUrl(url));
    this.backendTestResult.set(null);
  }

  backendSummaryLabel(): string {
    return backendDisplayLabel(this.nestjsUrl(), this.backendChoice());
  }

  canSaveBackend(): boolean {
    const url = normalizeNestjsUrl(this.nestjsUrl());
    if (!url) return false;
    if (this.backendChoice() === 'custom') {
      try {
        new URL(url);
        return true;
      } catch {
        return false;
      }
    }
    return true;
  }

  async testBackend(): Promise<void> {
    const url = normalizeNestjsUrl(this.nestjsUrl());
    if (!url) {
      this.backendTestResult.set({
        ok: false,
        message: 'Enter a server address first',
        latency: 0,
      });
      return;
    }
    this.backendTesting.set(true);
    this.backendTestResult.set(null);
    try {
      const result = await this.nls().backend.ping(url);
      this.backendTestResult.set({
        ok: result.ok,
        message: result.ok
          ? `Connected (${result.statusCode})`
          : result.message || 'Could not reach server',
        latency: result.latency,
      });
    } catch (err: any) {
      this.backendTestResult.set({
        ok: false,
        message: err?.message || 'Test failed',
        latency: 0,
      });
    } finally {
      this.backendTesting.set(false);
    }
  }

  async saveBackend(): Promise<void> {
    if (!this.canSaveBackend()) {
      this.toast.show('Enter a valid backend URL', 'error');
      return;
    }
    this.backendSaving.set(true);
    try {
      const url = normalizeNestjsUrl(this.nestjsUrl());
      await this.nls().config.set({ nestjsUrl: url });
      this.nestjsUrl.set(url);
      this.backendChoice.set(matchBackendChoice(url));
      void this.loadPlatformIntegrations();
      this.toast.show('Account server saved', 'info', 2000);
    } catch (err: any) {
      this.toast.show(err?.message || 'Could not save', 'error');
    } finally {
      this.backendSaving.set(false);
    }
  }

  async runtimeControl(action: 'start' | 'stop' | 'restart'): Promise<void> {
    this.runtimeAction.set(action);
    try {
      await this.nls().runtime[action]();
      await this.refreshRuntimeStatus();
      this.toast.show(`Runtime ${action === 'stop' ? 'stopped' : action + 'ed'}`, 'info', 2000);
    } catch (err: any) {
      this.toast.show(err?.message || `Failed to ${action} runtime`, 'error');
    } finally {
      this.runtimeAction.set('idle');
    }
  }

  async loadRuntimeLogs(): Promise<void> {
    try {
      const lines = await this.nls().runtime.getLogs(80);
      this.runtimeLogs.set(lines);
      this.showRuntimeLogs.set(true);
    } catch {
      this.toast.show('Could not load runtime logs', 'error');
    }
  }

  async resetPythonEnvironment(): Promise<void> {
    if (!confirm('Reset the Python environment? You will need to run setup again.')) {
      return;
    }
    this.envResetting.set(true);
    try {
      await this.nls().setup.reset();
      this.toast.show('Environment reset. Opening setup wizard…', 'info', 3000);
      this.router.navigate(['/setup']);
    } catch (err: any) {
      this.toast.show(err?.message || 'Reset failed', 'error');
    } finally {
      this.envResetting.set(false);
    }
  }

  async applyPermissionProfile(profileId: string): Promise<void> {
    try {
      await this.nls().permissions.applyProfile(profileId);
      this.activePermissionProfile.set(profileId);
      this.toast.show('Permission profile applied', 'info', 2000);
    } catch (err: any) {
      this.toast.show(err?.message || 'Could not apply profile', 'error');
    }
  }

  async resetPermissions(): Promise<void> {
    if (!confirm('Clear all saved permission decisions? You will be prompted again when needed.')) {
      return;
    }
    try {
      await this.nls().permissions.reset();
      this.activePermissionProfile.set(null);
      this.toast.show('Permissions reset', 'info', 2000);
    } catch (err: any) {
      this.toast.show(err?.message || 'Could not reset permissions', 'error');
    }
  }

  formatUptime(seconds: number): string {
    if (seconds < 60) return `${seconds}s`;
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    if (m < 60) return `${m}m ${s}s`;
    const h = Math.floor(m / 60);
    return `${h}h ${m % 60}m`;
  }

  private async loadDesktopSettings(): Promise<void> {
    try {
      const cfg = await this.nls().config.get();
      const url = normalizeNestjsUrl(cfg.nestjsUrl || 'https://api.babo.agency');
      this.nestjsUrl.set(url);
      this.backendChoice.set(matchBackendChoice(url));
      this.runtimePort.set(cfg.runtimePort ?? 9222);
      this.setupComplete.set(!!cfg.setupComplete);
      void this.loadPlatformIntegrations();
    } catch { /* ignore */ }

    try {
      const check = await this.nls().setup.check();
      this.venvReady.set(!!check.venvReady);
      this.setupComplete.set(!!check.setupComplete);
    } catch { /* ignore */ }

    try {
      const profiles = await this.nls().permissions.getProfiles();
      this.permissionProfiles.set(profiles ?? []);
    } catch { /* ignore */ }

    await this.refreshRuntimeStatus();
  }

  private loadWebSettings(): void {
    this.http.get<WebAppearanceSettings>(`${this.API}/settings`).subscribe({
      next: (s) => {
        const merged = { ...this.webSettings(), ...s };
        if (s.theme === 'light' || s.theme === 'dark' || s.theme === 'system') {
          this.themeService.setMode(s.theme);
        }
        this.webSettings.set(merged);
      },
      error: () => { /* endpoint optional */ },
    });
  }

  private async loadAppVersion(): Promise<void> {
    if (this.platform.isElectron) {
      try {
        this.appVersion.set(await this.nls().getVersion());
        return;
      } catch { /* fall through */ }
    }
    this.appVersion.set('web');
  }

  private startRuntimePolling(): void {
    void this.refreshRuntimeStatus();
    this.runtimePollTimer = setInterval(() => void this.refreshRuntimeStatus(), 5000);
  }

  private async refreshRuntimeStatus(): Promise<void> {
    try {
      const status = await this.nls().runtime.getStatus();
      this.runtimeStatus.set(status);
      if (status?.port) {
        this.runtimePort.set(status.port);
      }
    } catch {
      this.runtimeStatus.set(null);
    }
  }

  private nls(): any {
    return (window as any).nls;
  }
}
