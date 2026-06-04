import { Component, OnInit, computed, inject, output, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import type {
  CapabilityProfile,
  CapabilityScan,
  CapabilityTier,
  ModelFitSnapshot,
} from '../../features/setup/capability-profile.model';
import {
  CLOUD_PROVIDERS,
  baboCloudModelsForUser,
  isBaboHostedModelId,
  matchCloudProvider,
  resolveBaboCloudModelId,
  stripInferenceV1Suffix,
} from '../../features/setup/setup-inference.util';
import { AgentModelService } from '../../core/services/agent-model.service';
import { PlatformIntegrationsService } from '../../core/services/platform-integrations.service';
import { applyBaboCloudPlacements } from '../../features/setup/setup-cloud.util';

interface ExpRow {
  label: string;
  value: string;
  ok: boolean;
}

interface SetupConfig {
  inferenceUrl: string;
  inferenceModel: string;
  inferenceApiKey: string;
}

@Component({
  selector: 'app-capability-settings-panel',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './capability-settings-panel.component.html',
  styleUrl: './capability-settings-panel.component.scss',
})
export class CapabilitySettingsPanelComponent implements OnInit {
  private readonly agentModels = inject(AgentModelService);
  private readonly platformIntegrations = inject(PlatformIntegrationsService);

  /** Emitted after profile is saved and applied to the runtime. */
  saved = output<void>();

  scan = signal<CapabilityScan | null>(null);
  scanLoading = signal(false);
  profile = signal<CapabilityProfile | null>(null);

  lanHost = '';
  lanSshUser = '';
  lanSshPort = '';
  lanSshPassword = '';
  lanProbing = signal(false);
  lanModelFitLoading = signal(false);

  ambientVisionOn = signal(false);
  codeSearchOn = signal(true);
  brainTier = signal<CapabilityTier>('hosted_babo');
  voiceTier = signal<CapabilityTier>('self_local');
  visualTier = signal<CapabilityTier>('off');
  cloudProviderId = signal('openrouter');
  recommendedBrainTier: CapabilityTier = 'hosted_babo';
  delegateUsesPrimary = signal(true);
  private savedProfileSnapshot: CapabilityProfile | null = null;

  testing = signal(false);
  testResult = signal<{ ok: boolean; message: string; latency: number } | null>(null);
  saving = signal(false);
  saveError = signal<string | null>(null);

  readonly cloudProviders = CLOUD_PROVIDERS;
  readonly baboCloudModels = computed(() =>
    baboCloudModelsForUser({
      hostedGx10Available:
        this.platformIntegrations.capabilities()?.inference?.hostedGx10Available,
      hostedGx10Label:
        this.platformIntegrations.capabilities()?.inference?.hostedGx10Label,
    }),
  );

  readonly brainCards = [
    {
      tier: 'hosted_babo' as CapabilityTier,
      title: 'Babo Cloud',
      subtitle: 'Qwen, GPT, Claude via your Babo account',
      glyph: '☁',
    },
    {
      tier: 'self_local' as CapabilityTier,
      title: 'This computer',
      subtitle: 'Ollama on this PC',
      glyph: '◻',
    },
    {
      tier: 'self_lan' as CapabilityTier,
      title: 'My server',
      subtitle: 'vLLM or compatible server on your LAN',
      glyph: '⎔',
    },
    {
      tier: 'byok_cloud' as CapabilityTier,
      title: 'Your API key',
      subtitle: 'OpenRouter, OpenAI, Anthropic, …',
      glyph: '🔑',
    },
  ];

  voiceOptions = [
    { tier: 'self_local' as CapabilityTier, shortLabel: 'This PC' },
    { tier: 'self_lan' as CapabilityTier, shortLabel: 'My server' },
    { tier: 'off' as CapabilityTier, shortLabel: 'Off' },
  ];

  config: SetupConfig = {
    inferenceUrl: 'https://openrouter.ai/api',
    inferenceModel: 'openai/gpt-4o-mini',
    inferenceApiKey: '',
  };

  async ngOnInit(): Promise<void> {
    await this.platformIntegrations.refresh();
    await this.load();
    if (!this.scan()) {
      void this.runDeviceScan();
    }
  }

  experienceRows(): ExpRow[] {
    const p = this.profile();
    if (!p) return [];
    const brain =
      p.inference.tier === 'off'
        ? 'Not configured'
        : p.inference.tier === 'hosted_babo'
          ? this.labelForModel(p.inference.model)
          : p.inference.model || tierLabel(p.inference.tier);
    const voice = p.transcribe.tier === 'off' ? 'Off' : tierLabel(p.transcribe.tier);
    const ambient = this.ambientVisionOn() ? tierLabel(this.visualTier()) : 'Off';
    const code = this.codeSearchOn() ? 'On' : 'Off';
    return [
      {
        label: 'Thinking',
        value: brain,
        ok: p.inference.tier !== 'off',
      },
      {
        label: 'Sub-agents',
        value: this.delegateUsesPrimary()
          ? 'Same as primary'
          : this.delegateModelLabel() || 'Custom',
        ok: p.inference.tier !== 'off',
      },
      { label: 'Voice', value: voice, ok: p.transcribe.tier !== 'off' },
      { label: 'Screen awareness', value: ambient, ok: this.ambientVisionOn() },
      { label: 'Code search', value: code, ok: this.codeSearchOn() },
    ];
  }

  async load(): Promise<void> {
    try {
      const cfg = await this.nls().config.get();
      this.config = {
        inferenceUrl: stripInferenceV1Suffix(
          cfg.inferenceUrl || cfg.vllmUrl || this.config.inferenceUrl,
        ),
        inferenceModel: cfg.inferenceModel || cfg.hfModel || this.config.inferenceModel,
        inferenceApiKey: cfg.inferenceApiKey || '',
      };
      if (cfg.capabilityProfile) {
        const prof = cfg.capabilityProfile as CapabilityProfile;
        this.profile.set(prof);
        this.savedProfileSnapshot = JSON.parse(JSON.stringify(prof));
        this.applyProfileToUi(prof);
        if (prof.scan) {
          this.scan.set(prof.scan);
        }
        await this.refreshRecommendedTier();
      } else {
        await this.runDeviceScan();
        const scan = this.scan();
        if (scan) {
          const recommended = await this.nls().capabilities.recommend(scan);
          this.profile.set(recommended);
          this.savedProfileSnapshot = JSON.parse(JSON.stringify(recommended));
          this.applyProfileToUi(recommended);
        }
      }
    } catch {
      /* use defaults */
    }
  }

  localModelFit = () => this.scan()?.modelFit?.local ?? null;
  lanModelFit = () => this.scan()?.modelFit?.lan ?? null;

  resolvedLanSshUser(): string | undefined {
    const host = this.lanHost.trim();
    if (host.includes('@')) return host.split('@')[0]?.trim() || undefined;
    const u = this.lanSshUser.trim();
    return u || undefined;
  }

  lanSshOptions(): { user?: string; port?: number; password?: string } | undefined {
    const user = this.resolvedLanSshUser();
    if (!user) return undefined;
    const port = parseInt(this.lanSshPort.trim(), 10);
    const password = this.lanSshPassword.trim();
    return {
      user,
      port: Number.isFinite(port) && port > 0 ? port : undefined,
      password: password || undefined,
    };
  }

  fitLevelLabel(level: string): string {
    switch (level) {
      case 'perfect':
        return 'Perfect fit';
      case 'good':
        return 'Good fit';
      case 'marginal':
        return 'Tight fit';
      default:
        return level;
    }
  }

  fitGpuHeadline(fit: ModelFitSnapshot): string {
    if (fit.memoryLabel?.trim()) {
      return `${fit.gpuName} · ${fit.memoryLabel.trim()}`;
    }
    const gb = fit.vramGb;
    const mem =
      Number.isFinite(gb) && gb > 0
        ? fit.unifiedMemory
          ? `${gb} GB unified memory`
          : `${gb} GB VRAM`
        : fit.unifiedMemory
          ? 'Unified memory'
          : 'VRAM unknown';
    return `${fit.gpuName} · ${mem}`;
  }

  async runLanProbe(): Promise<void> {
    if (!this.lanHost.trim()) return;
    this.lanProbing.set(true);
    try {
      const prev = this.scan();
      const full = (await this.nls().capabilities.probeLan(
        this.lanHost.trim(),
        undefined,
        this.lanSshOptions(),
        prev?.modelFit,
      )) as CapabilityScan;
      const device = prev?.device ?? full.device;
      this.scan.set({
        ...full,
        device,
        modelFit: {
          local: prev?.modelFit?.local ?? full.modelFit?.local,
          lan: full.modelFit?.lan ?? prev?.modelFit?.lan,
        },
      });
      const p = this.profile();
      if (p) {
        p.scan = this.scan() ?? undefined;
        this.profile.set({ ...p });
      }
      await this.refreshRecommendedTier();
    } finally {
      this.lanProbing.set(false);
    }
  }

  async runLanModelFit(): Promise<void> {
    if (!this.lanHost.trim() || !this.resolvedLanSshUser()) return;
    this.lanModelFitLoading.set(true);
    try {
      const snap = (await this.nls().capabilities.modelFitRemote(
        this.lanHost.trim(),
        this.lanSshOptions(),
      )) as ModelFitSnapshot;
      const prev = this.scan();
      if (prev) {
        this.scan.set({ ...prev, modelFit: { ...prev.modelFit, lan: snap } });
      }
      await this.refreshRecommendedTier();
    } finally {
      this.lanModelFitLoading.set(false);
    }
  }

  async runDeviceScan(): Promise<void> {
    this.scanLoading.set(true);
    try {
      const scan = await this.nls().capabilities.scanDevice();
      const prev = this.scan();
      this.scan.set({
        ...scan,
        lan: prev?.lan ?? [],
        modelFit: {
          local: scan.modelFit?.local ?? prev?.modelFit?.local,
          lan: prev?.modelFit?.lan,
        },
      });
      const p = this.profile();
      if (p) {
        p.scan = this.scan() ?? undefined;
        this.profile.set({ ...p });
      }
      await this.refreshRecommendedTier();
    } finally {
      this.scanLoading.set(false);
    }
  }

  private async refreshRecommendedTier(): Promise<void> {
    const scan = this.scan();
    if (!scan) return;
    try {
      const recommended = await this.nls().capabilities.recommend(scan);
      this.recommendedBrainTier = recommended.inference.tier;
    } catch {
      /* keep default */
    }
  }

  brainServerUrl(): string {
    return stripInferenceV1Suffix(this.profile()?.inference.url ?? '');
  }

  setBrainServerUrl(url: string): void {
    const p = this.profile();
    if (!p) return;
    p.inference.url = stripInferenceV1Suffix(url);
    this.syncInferenceLegacy();
    this.profile.set({ ...p });
  }

  brainModelLabel(): string {
    return this.profile()?.inference.model ?? '';
  }

  applyProfileToUi(p: CapabilityProfile): void {
    this.brainTier.set(p.inference.tier);
    if (p.inference.tier === 'hosted_babo' && !isBaboHostedModelId(p.inference.model ?? '')) {
      p.inference.model = resolveBaboCloudModelId(p.inference.model);
    }
    this.voiceTier.set(p.transcribe.tier);
    if (p.inference.url) {
      p.inference.url = stripInferenceV1Suffix(p.inference.url);
    }
    this.cloudProviderId.set(matchCloudProvider(p.inference.url ?? ''));
    const vcOn =
      p.visualCortex.tier !== 'off' && (p.visualCortex.strategy ?? 'off') !== 'off';
    this.ambientVisionOn.set(vcOn);
    this.visualTier.set(vcOn ? p.visualCortex.tier : 'off');
    this.codeSearchOn.set(p.embeddings.tier !== 'off');
    const del = p.delegateInference;
    this.delegateUsesPrimary.set(!del || del.usePrimaryModel !== false);
    this.syncInferenceLegacy();
  }

  baboCloudModelActive(id: string): boolean {
    const m = this.profile()?.inference.model ?? '';
    return resolveBaboCloudModelId(m) === id;
  }

  selectBaboCloudModel(id: string): void {
    const p = this.profile();
    if (!p) return;
    p.inference.model = id;
    this.config.inferenceModel = id;
    this.profile.set({ ...p });
    this.testResult.set(null);
  }

  delegateModelActive(id: string): boolean {
    const del = this.profile()?.delegateInference;
    const m = del?.model ?? this.profile()?.inference.model ?? '';
    return resolveBaboCloudModelId(m) === id;
  }

  delegateModelLabel(): string {
    const p = this.profile();
    if (!p) return '';
    const del = p.delegateInference;
    const m = del?.usePrimaryModel === false && del.model
      ? del.model
      : p.inference.model;
    return this.labelForModel(m);
  }

  selectDelegateModel(id: string): void {
    const p = this.profile();
    if (!p) return;
    p.delegateInference = { usePrimaryModel: false, model: id };
    this.profile.set({ ...p });
  }

  setDelegateUsesPrimary(on: boolean): void {
    this.delegateUsesPrimary.set(on);
    const p = this.profile();
    if (!p) return;
    if (on) {
      p.delegateInference = { usePrimaryModel: true };
    } else {
      p.delegateInference = {
        usePrimaryModel: false,
        model: resolveBaboCloudModelId(p.inference.model),
      };
    }
    this.profile.set({ ...p });
  }

  private labelForModel(model?: string): string {
    return this.agentModels.labelFor(model ?? '');
  }

  private needsRuntimeRestart(
    prev: CapabilityProfile | null,
    next: CapabilityProfile,
  ): boolean {
    if (!prev) return true;
    if (prev.inference.tier !== next.inference.tier) return true;
    const prevUrl = stripInferenceV1Suffix(prev.inference.url ?? '');
    const nextUrl = stripInferenceV1Suffix(next.inference.url ?? '');
    if (prevUrl !== nextUrl) return true;
    if (prev.visualCortex.tier !== next.visualCortex.tier) return true;
    if ((prev.visualCortex.strategy ?? 'off') !== (next.visualCortex.strategy ?? 'off')) {
      return true;
    }
    if (prev.transcribe.tier !== next.transcribe.tier) return true;
    if (prev.embeddings.tier !== next.embeddings.tier) return true;
    return false;
  }

  selectCloudProvider(id: string): void {
    const p = this.profile();
    const prov = CLOUD_PROVIDERS.find((x) => x.id === id);
    if (!p || !prov) return;
    this.cloudProviderId.set(id);
    p.inference.url = prov.baseUrl;
    p.inference.model = prov.defaultModel;
    this.syncInferenceLegacy();
    this.profile.set({ ...p });
    this.testResult.set(null);
  }

  onBrainTierChange(tier: CapabilityTier): void {
    this.brainTier.set(tier);
    const p = this.profile();
    if (!p) return;
    p.inference.tier = tier;
    this.testResult.set(null);

    if (tier === 'self_lan') {
      const inf = this.scan()?.lan.find((x) => x.kind === 'inference' && x.healthy);
      if (inf) {
        p.inference.url = stripInferenceV1Suffix(inf.url);
        p.inference.model = inf.modelIds?.[0] ?? p.inference.model;
      } else {
        p.inference.url = p.inference.url || 'http://192.168.1.50:8000';
      }
    } else if (tier === 'self_local') {
      p.inference.url = 'http://127.0.0.1:11434';
      p.inference.model = p.inference.model || 'llama3.2';
    } else if (tier === 'byok_cloud') {
      this.selectCloudProvider(this.cloudProviderId());
    } else if (tier === 'hosted_babo') {
      p.inference.url = '';
      p.inference.model = resolveBaboCloudModelId(p.inference.model);
      this.config.inferenceModel = p.inference.model;
    }
    this.syncInferenceLegacy();
    this.profile.set({ ...p });
  }

  onVoiceTierChange(tier: CapabilityTier): void {
    this.voiceTier.set(tier);
    const p = this.profile();
    if (!p) return;
    p.transcribe.tier = tier;
    if (tier === 'self_lan') {
      const t = this.scan()?.lan.find((x) => x.kind === 'transcribe' && x.healthy);
      if (t) p.transcribe.url = t.url;
    }
    this.profile.set({ ...p });
  }

  toggleAmbient(on: boolean): void {
    this.ambientVisionOn.set(on);
    const p = this.profile();
    if (!p) return;
    if (!on) {
      p.visualCortex = { tier: 'off', strategy: 'off' };
      this.visualTier.set('off');
    } else {
      p.visualCortex = {
        tier: 'self_local',
        strategy: 'dedicated_vlm_local',
        modelPreference: 'auto',
      };
      this.visualTier.set('self_local');
    }
    this.profile.set({ ...p });
  }

  setVisualLocal(): void {
    const p = this.profile();
    if (!p) return;
    p.visualCortex = {
      tier: 'self_local',
      strategy: 'dedicated_vlm_local',
      modelPreference: 'auto',
    };
    this.visualTier.set('self_local');
    this.profile.set({ ...p });
  }

  setVisualLan(): void {
    const p = this.profile();
    if (!p) return;
    const v = this.scan()?.lan.find((x) => x.kind === 'vision' && x.healthy);
    p.visualCortex = {
      tier: 'self_lan',
      strategy: 'dedicated_vlm_lan',
      url: v?.url ?? 'http://192.168.1.50:8450',
    };
    this.visualTier.set('self_lan');
    this.profile.set({ ...p });
  }

  toggleCodeSearch(on: boolean): void {
    this.codeSearchOn.set(on);
    const p = this.profile();
    if (!p) return;
    p.embeddings = on
      ? { tier: 'self_local', reason: 'Semantic code search on this computer' }
      : { tier: 'off', reason: 'Disabled' };
    this.profile.set({ ...p });
  }

  canLocalVision(): boolean {
    return (this.scan()?.device.vramGb ?? 0) >= 6;
  }

  hasLanVision(): boolean {
    return !!this.scan()?.lan.some((x) => x.kind === 'vision' && x.healthy);
  }

  syncInferenceLegacy(): void {
    const p = this.profile();
    if (!p) return;
    if (p.inference.tier === 'hosted_babo') {
      if (p.inference.model) {
        this.config.inferenceModel = isBaboHostedModelId(p.inference.model)
          ? p.inference.model
          : resolveBaboCloudModelId(p.inference.model);
      }
      return;
    }
    if (!p.inference.url) return;
    this.config.inferenceUrl = stripInferenceV1Suffix(p.inference.url);
    if (p.inference.model) this.config.inferenceModel = p.inference.model;
  }

  async testBrain(): Promise<void> {
    const p = this.profile();
    if (!p || p.inference.tier === 'hosted_babo') {
      if (p?.inference.tier === 'hosted_babo') {
        this.testResult.set({
          ok: true,
          message: 'Babo Cloud — model saved with your account',
          latency: 0,
        });
      }
      return;
    }
    const url = stripInferenceV1Suffix(p.inference.url || this.config.inferenceUrl);
    if (!url) {
      this.testResult.set({ ok: false, message: 'Enter a server address first', latency: 0 });
      return;
    }
    this.testing.set(true);
    this.testResult.set(null);
    try {
      const result = await this.nls().capabilities.testInference(
        url,
        this.config.inferenceApiKey || undefined,
      );
      this.testResult.set(result);
      if (result.ok) {
        if (result.models?.length) {
          p.inference.model = result.models[0];
        }
        p.inference.url = url;
        this.syncInferenceLegacy();
        this.profile.set({ ...p });
      }
    } catch (err: any) {
      this.testResult.set({ ok: false, message: err?.message || 'Test failed', latency: 0 });
    } finally {
      this.testing.set(false);
    }
  }

  async save(): Promise<void> {
    const p = this.profile();
    if (!p) return;
    if (p.inference.url) {
      p.inference.url = stripInferenceV1Suffix(p.inference.url);
    }
    if (this.scan()) {
      p.scan = this.scan() ?? undefined;
    }
    if (this.delegateUsesPrimary()) {
      p.delegateInference = { usePrimaryModel: true };
    } else if (!p.delegateInference?.model) {
      p.delegateInference = {
        usePrimaryModel: false,
        model: resolveBaboCloudModelId(p.inference.model),
      };
    }
    if (p.inference.tier === 'hosted_babo') {
      if (!isBaboHostedModelId(p.inference.model ?? '')) {
        p.inference.model = resolveBaboCloudModelId(p.inference.model);
      }
      this.config.inferenceModel =
        p.inference.model ?? resolveBaboCloudModelId(p.inference.model);
    }
    this.saving.set(true);
    this.saveError.set(null);
    try {
      this.syncInferenceLegacy();
      let profileToApply = p;
      if (p.inference.tier === 'hosted_babo' || p.inference.tier === 'byok_cloud') {
        const cfg = await this.nls().config.get();
        profileToApply = applyBaboCloudPlacements(p, cfg.nestjsUrl || '');
        this.profile.set(profileToApply);
      }
      await this.nls().capabilities.applyProfile(profileToApply);
      await this.nls().config.set({
        capabilityProfile: profileToApply,
        inferenceApiKey: this.config.inferenceApiKey,
        inferenceModel: profileToApply.inference.model || this.config.inferenceModel,
      });

      const primaryModel = profileToApply.inference.model || this.config.inferenceModel;
      const del = profileToApply.delegateInference;
      const delegateUsePrimary = !del || del.usePrimaryModel !== false;
      try {
        await this.agentModels.hotReloadInference({
          hf_model: primaryModel,
          delegate_hf_model: delegateUsePrimary ? null : (del?.model ?? null),
          delegate_use_primary: delegateUsePrimary,
          inference_api_key: this.config.inferenceApiKey || undefined,
        });
      } catch {
        /* runtime may be stopped */
      }

      const needsRestart = this.needsRuntimeRestart(this.savedProfileSnapshot, profileToApply);
      if (
        this.ambientVisionOn() &&
        profileToApply.visualCortex.strategy === 'dedicated_vlm_local'
      ) {
        try {
          await this.nls().capabilities.prefetchVision();
        } catch {
          /* non-fatal */
        }
      }
      if (needsRestart) {
        try {
          await this.nls().runtime.restart();
        } catch {
          /* runtime may not be running yet */
        }
      }
      this.savedProfileSnapshot = JSON.parse(JSON.stringify(profileToApply));
      await this.agentModels.refreshFromConfig();
      this.saved.emit();
    } catch (err: any) {
      this.saveError.set(err?.message || 'Could not save capabilities');
    } finally {
      this.saving.set(false);
    }
  }

  private nls(): any {
    return (window as any).nls;
  }
}

function tierLabel(tier: CapabilityTier): string {
  switch (tier) {
    case 'self_local':
      return 'This computer';
    case 'self_lan':
      return 'My server';
    case 'byok_cloud':
      return 'Cloud';
    case 'hosted_babo':
      return 'Babo hosted';
    default:
      return 'Off';
  }
}
