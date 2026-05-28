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
import { ToastService } from '../../shared/toast/toast.service';
import { Day1CoachService } from '../../shared/onboarding/day1-coach.service';
import { PlatformService } from '../../core/services/platform.service';
import { ThemeService, ThemeMode } from '../../core/services/theme.service';
import { CapabilitySettingsPanelComponent } from '../../shared/capability-settings-panel/capability-settings-panel.component';
import { AgentModelService } from '../../core/services/agent-model.service';
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

  readonly backendChoices = BACKEND_CHOICES;

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

  sections = computed(() => {
    if (this.platform.isElectron) {
      return [
        { id: 'models', label: 'Models & AI' },
        { id: 'account', label: 'Account' },
        { id: 'system', label: 'System' },
        { id: 'permissions', label: 'Permissions' },
        { id: 'appearance', label: 'Appearance' },
        { id: 'general', label: 'General' },
      ];
    }
    return [
      { id: 'appearance', label: 'Appearance' },
      { id: 'keys', label: 'API keys' },
      { id: 'general', label: 'General' },
    ];
  });

  toolsLink = computed(() => {
    const id = this.router.url.match(/\/([a-f0-9-]{36})/i)?.[1];
    return id ? ['/tools', id] : ['/dashboard'];
  });

  private runtimePollTimer: ReturnType<typeof setInterval> | null = null;

  constructor(
    private http: HttpClient,
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
      this.loadWebSettings();
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
