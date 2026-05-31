import { HttpClient } from '@angular/common/http';
import { Injectable, computed, inject, signal } from '@angular/core';
import { firstValueFrom } from 'rxjs';
import type { CapabilityTier } from '../../features/setup/capability-profile.model';
import {
  isBaboHostedModelId,
  matchCloudProvider,
  resolveBaboCloudModelId,
} from '../../features/setup/setup-inference.util';
import { readBaboBoot } from '../desktop-boot';
import { environment } from '../../../environments/environment';
import {
  labelForModelId,
  mergeModelCatalog,
  parseOpenAiModelList,
  shouldOfferBaboCloudModels,
} from './model-catalog.util';
import { ApiService } from './api.service';
import { PlatformService } from './platform.service';

export interface ModelPickerOption {
  id: string;
  label: string;
}

export interface InferenceHotReloadPayload {
  hf_model?: string;
  delegate_hf_model?: string | null;
  delegate_use_primary?: boolean;
  inference_api_key?: string;
}

export interface AgentSessionInference {
  orchestratorModelId: string | null;
  delegateModelId: string | null;
  delegateLockToOrchestrator: boolean;
  /** One-shot override for the next message (Cursor-style). */
  requestOverrideId: string | null;
}

export interface AgentInferenceSettingsResponse {
  orchestrator_model: string | null;
  delegate_model: string | null;
  delegate_lock_orchestrator: boolean;
}

/**
 * Model selection hierarchy:
 * 1. Install default (capability profile / nls-config)
 * 2. Agent session default (orchestrator + optional delegate)
 * 3. Per-request override (chat picker, one message)
 */
@Injectable({ providedIn: 'root' })
export class AgentModelService {
  private readonly http = inject(HttpClient);
  private readonly platform = inject(PlatformService);
  private readonly api = inject(ApiService);

  readonly defaultModelId = signal('');
  readonly defaultModelLabel = signal('');
  readonly inferenceTier = signal<CapabilityTier>('hosted_babo');
  readonly catalog = signal<ModelPickerOption[]>([]);
  readonly loaded = signal(false);
  readonly loadingModels = signal(false);

  /** Currently focused agent in chat UI. */
  readonly activeAgentId = signal<string | null>(null);
  readonly advancedMode = signal(false);

  /** Bumped when per-agent session model state changes (Map is not a signal). */
  private readonly sessionEpoch = signal(0);

  private readonly sessionByAgent = new Map<string, AgentSessionInference>();

  /** @deprecated use requestOverrideId via session; kept for template compat */
  readonly chatModelId = computed(() => {
    this.sessionEpoch();
    const id = this.activeAgentId();
    if (!id) return null;
    return this.sessionFor(id).requestOverrideId;
  });

  readonly sessionOrchestratorModelId = computed(() => {
    this.sessionEpoch();
    const id = this.activeAgentId();
    if (!id) return null;
    return this.sessionFor(id).orchestratorModelId;
  });

  readonly sessionDelegateModelId = computed(() => {
    this.sessionEpoch();
    const id = this.activeAgentId();
    if (!id) return null;
    return this.sessionFor(id).delegateModelId;
  });

  readonly effectiveModelId = computed(() => {
    if (this.creationMode()) {
      this.creationEpoch();
      return this.creationOrchestratorId();
    }
    this.sessionEpoch();
    const id = this.activeAgentId();
    if (!id) return this.defaultModelId();
    const session = this.sessionFor(id);
    return (
      session.requestOverrideId ??
      session.orchestratorModelId ??
      this.defaultModelId()
    );
  });

  readonly effectiveModelLabel = computed(() =>
    labelForModelId(this.effectiveModelId()),
  );

  readonly effectiveDelegateModelId = computed(() => {
    if (this.creationMode()) {
      this.creationEpoch();
      return this.creationEffectiveDelegateId();
    }
    this.sessionEpoch();
    const id = this.activeAgentId();
    const orch = this.effectiveModelId();
    if (!id) return orch;
    const session = this.sessionFor(id);
    if (session.delegateLockToOrchestrator) return orch;
    return session.delegateModelId ?? orch;
  });

  readonly delegateLockToOrchestrator = computed(() => {
    if (this.creationMode()) {
      this.creationEpoch();
      return this.creationDraft?.delegateLockToOrchestrator !== false;
    }
    this.sessionEpoch();
    const id = this.activeAgentId();
    if (!id) return true;
    return this.sessionFor(id).delegateLockToOrchestrator;
  });

  readonly pickerOptions = computed(() => this.catalog());

  readonly showPicker = computed(
    () => this.loaded() && this.catalog().length > 0,
  );

  /** Draft inference defaults while creating a new agent (before runtime id exists). */
  readonly creationMode = signal(false);
  private creationDraft: AgentSessionInference | null = null;
  private readonly creationEpoch = signal(0);

  readonly creationHasCustomOrchestrator = computed(() => {
    this.creationEpoch();
    if (!this.creationMode()) return false;
    const id = this.creationDraft?.orchestratorModelId;
    return !!id && id !== this.defaultModelId();
  });

  readonly creationCompositionHint = computed(() => {
    this.creationEpoch();
    if (!this.creationMode()) return '';
    const orch = this.creationOrchestratorId();
    const orchLabel = this.labelFor(orch);
    if (this.creationDraft?.delegateLockToOrchestrator !== false) {
      return `Orchestrator and sub-agents will use ${orchLabel}`;
    }
    const del = this.creationEffectiveDelegateId();
    const delLabel = this.labelFor(del);
    return `Orchestrator: ${orchLabel} · Sub-agents: ${delLabel}`;
  });

  readonly hasSessionDefault = computed(() => {
    if (this.creationMode()) {
      return this.creationHasCustomOrchestrator();
    }
    this.sessionEpoch();
    const id = this.activeAgentId();
    if (!id) return false;
    return !!this.sessionFor(id).orchestratorModelId;
  });

  readonly hasRequestOverride = computed(() => {
    this.sessionEpoch();
    const id = this.activeAgentId();
    if (!id) return false;
    return !!this.sessionFor(id).requestOverrideId;
  });

  private bumpSession(): void {
    this.sessionEpoch.update((n) => n + 1);
  }

  private bumpCreation(): void {
    this.creationEpoch.update((n) => n + 1);
  }

  private creationOrchestratorId(): string {
    return this.creationDraft?.orchestratorModelId ?? this.defaultModelId();
  }

  private creationEffectiveDelegateId(): string {
    const orch = this.creationOrchestratorId();
    if (!this.creationDraft || this.creationDraft.delegateLockToOrchestrator) {
      return orch;
    }
    return this.creationDraft.delegateModelId ?? orch;
  }

  labelFor(modelId: string): string {
    return labelForModelId(modelId);
  }

  bindAgent(agentId: string): void {
    this.activeAgentId.set(agentId);
    if (!this.sessionByAgent.has(agentId)) {
      this.sessionByAgent.set(agentId, this.emptySession());
    }
    void this.loadAgentSessionModels(agentId);
  }

  setChatModel(modelId: string | null): void {
    const agentId = this.activeAgentId();
    if (!agentId) return;
    const session = this.sessionFor(agentId);
    const def = this.defaultModelId();
    const sessionDefault = session.orchestratorModelId ?? def;
    if (!modelId || modelId === sessionDefault) {
      session.requestOverrideId = null;
    } else {
      session.requestOverrideId = modelId;
    }
    this.sessionByAgent.set(agentId, { ...session });
    this.bumpSession();
  }

  resetChatModel(): void {
    const agentId = this.activeAgentId();
    if (!agentId) return;
    const session = this.sessionFor(agentId);
    session.requestOverrideId = null;
    this.sessionByAgent.set(agentId, { ...session });
    this.bumpSession();
  }

  async setSessionOrchestratorModel(modelId: string | null): Promise<void> {
    const agentId = this.activeAgentId();
    if (!agentId) return;
    const session = this.sessionFor(agentId);
    session.orchestratorModelId = modelId;
    session.requestOverrideId = null;
    this.sessionByAgent.set(agentId, { ...session });
    this.bumpSession();
    await this.persistAgentSessionModels(agentId);
  }

  async setSessionDelegateModel(modelId: string | null): Promise<void> {
    const agentId = this.activeAgentId();
    if (!agentId) return;
    const session = this.sessionFor(agentId);
    session.delegateModelId = modelId;
    this.sessionByAgent.set(agentId, { ...session });
    this.bumpSession();
    await this.persistAgentSessionModels(agentId);
  }

  async setDelegateLockToOrchestrator(locked: boolean): Promise<void> {
    const agentId = this.activeAgentId();
    if (!agentId) return;
    const session = this.sessionFor(agentId);
    session.delegateLockToOrchestrator = locked;
    this.sessionByAgent.set(agentId, { ...session });
    this.bumpSession();
    await this.persistAgentSessionModels(agentId);
  }

  toggleAdvancedMode(): void {
    this.advancedMode.update((v) => !v);
  }

  beginCreationMode(): void {
    this.creationMode.set(true);
    const def = this.defaultModelId();
    this.creationDraft = this.emptySession();
    if (def) {
      this.creationDraft.orchestratorModelId = def;
    }
    this.advancedMode.set(false);
    this.bumpCreation();
  }

  endCreationMode(): void {
    this.creationMode.set(false);
    this.creationDraft = null;
    this.advancedMode.set(false);
    this.bumpCreation();
  }

  setCreationOrchestratorModel(modelId: string | null): void {
    if (!this.creationDraft) return;
    const def = this.defaultModelId();
    this.creationDraft.orchestratorModelId =
      !modelId || modelId === def ? null : modelId;
    this.bumpCreation();
  }

  setCreationDelegateModel(modelId: string | null): void {
    if (!this.creationDraft) return;
    const orch = this.creationOrchestratorId();
    this.creationDraft.delegateModelId =
      !modelId || modelId === orch ? null : modelId;
    this.bumpCreation();
  }

  setCreationDelegateLock(locked: boolean): void {
    if (!this.creationDraft) return;
    this.creationDraft.delegateLockToOrchestrator = locked;
    this.bumpCreation();
  }

  async applyCreationDraftToAgent(runtimeAgentId: string): Promise<void> {
    const draft = this.creationDraft;
    if (!draft || !runtimeAgentId) {
      this.endCreationMode();
      return;
    }
    const defaultId = this.defaultModelId();
    const orch = draft.orchestratorModelId ?? defaultId;
    const snapshot: AgentSessionInference = {
      orchestratorModelId: orch || null,
      delegateModelId: draft.delegateModelId,
      delegateLockToOrchestrator: draft.delegateLockToOrchestrator,
      requestOverrideId: null,
    };
    this.endCreationMode();

    this.activeAgentId.set(runtimeAgentId);
    this.sessionByAgent.set(runtimeAgentId, { ...snapshot });
    this.bumpSession();

    if (orch) {
      try {
        await this.hotReloadInference({ hf_model: orch });
      } catch {
        /* runtime may still be starting */
      }
      await this.persistAgentSessionModels(runtimeAgentId);
    }
  }

  /** Model id to send on the wire (only when it differs from resolved backend default). */
  modelForOutgoingMessage(): string | undefined {
    const agentId = this.activeAgentId();
    if (!agentId) return undefined;
    const session = this.sessionFor(agentId);
    const effective = this.effectiveModelId();
    const backendDefault =
      session.orchestratorModelId ?? this.defaultModelId();
    if (effective === backendDefault) return undefined;
    return effective;
  }

  apiBase(): string {
    const boot = readBaboBoot();
    return boot?.apiUrl ?? environment.apiUrl;
  }

  runtimeBase(): string {
    const boot = readBaboBoot();
    if (boot?.runtimeUrl) return boot.runtimeUrl.replace(/\/+$/, '');
    const port = boot?.runtimePort ?? 9222;
    return `http://127.0.0.1:${port}`;
  }

  async refreshFromConfig(): Promise<void> {
    this.loadingModels.set(true);
    try {
      if (this.platform.isElectron && (window as any).nls?.config?.get) {
        await this.refreshFromDesktopConfig();
      } else {
        await this.refreshFromWebConfig();
      }
    } catch {
      /* keep prior */
    } finally {
      this.loaded.set(true);
      this.loadingModels.set(false);
    }
  }

  async hotReloadInference(payload: InferenceHotReloadPayload): Promise<void> {
    const body: InferenceHotReloadPayload = {};
    if (payload.hf_model) body.hf_model = payload.hf_model;
    if (payload.delegate_hf_model !== undefined) {
      body.delegate_hf_model = payload.delegate_hf_model;
    }
    if (payload.delegate_use_primary !== undefined) {
      body.delegate_use_primary = payload.delegate_use_primary;
    }
    if (payload.inference_api_key !== undefined) {
      body.inference_api_key = payload.inference_api_key;
    }
    if (!Object.keys(body).length) return;

    if (this.platform.isElectron && (window as any).nls?.runtime?.hotReloadInference) {
      await (window as any).nls.runtime.hotReloadInference(body);
      return;
    }

    const base = this.runtimeBase();
    await firstValueFrom(
      this.http.post(`${base}/admin/inference/hot-reload`, body),
    );
  }

  private emptySession(): AgentSessionInference {
    return {
      orchestratorModelId: null,
      delegateModelId: null,
      delegateLockToOrchestrator: true,
      requestOverrideId: null,
    };
  }

  private sessionFor(agentId: string): AgentSessionInference {
    return this.sessionByAgent.get(agentId) ?? this.emptySession();
  }

  private async loadAgentSessionModels(agentId: string): Promise<void> {
    try {
      const base = this.runtimeBase();
      const data = await firstValueFrom(
        this.http.get<AgentInferenceSettingsResponse>(
          `${base}/agents/${agentId}/inference`,
        ),
      );
      const session = this.sessionFor(agentId);
      session.orchestratorModelId = data.orchestrator_model || null;
      session.delegateModelId = data.delegate_model || null;
      session.delegateLockToOrchestrator =
        data.delegate_lock_orchestrator !== false;
      this.sessionByAgent.set(agentId, { ...session });
      this.bumpSession();
    } catch {
      /* agent may not exist yet */
    }
  }

  private async persistAgentSessionModels(agentId: string): Promise<void> {
    const session = this.sessionFor(agentId);
    const base = this.runtimeBase();
    const body: Record<string, unknown> = {
      orchestrator_model: session.orchestratorModelId,
      delegate_model: session.delegateModelId,
      delegate_lock_orchestrator: session.delegateLockToOrchestrator,
    };
    if (!session.orchestratorModelId) {
      body['clear_orchestrator'] = true;
    }
    if (!session.delegateModelId) {
      body['clear_delegate'] = true;
    }
    await firstValueFrom(
      this.http.patch(`${base}/agents/${agentId}/inference`, body),
    );
  }

  private async refreshFromDesktopConfig(): Promise<void> {
    const cfg = await (window as any).nls.config.get();
    const profile = cfg.capabilityProfile;
    const tier: CapabilityTier = profile?.inference?.tier ?? 'hosted_babo';
    this.inferenceTier.set(tier);

    let model =
      profile?.inference?.model || cfg.inferenceModel || cfg.hfModel || '';
    if (tier === 'hosted_babo' && !isBaboHostedModelId(model)) {
      model = resolveBaboCloudModelId(model);
    }

    const hasCloudApi = !!this.apiBase();
    let discovered: string[] = [];

    if (tier === 'byok_cloud' || tier === 'self_lan' || tier === 'self_local') {
      const url = profile?.inference?.url || cfg.inferenceUrl || '';
      const key = cfg.inferenceApiKey || '';
      if (url && (window as any).nls?.capabilities?.testInference) {
        try {
          const result = await (window as any).nls.capabilities.testInference(
            url,
            key || undefined,
          );
          if (result.ok && result.models?.length) {
            discovered = result.models;
          }
        } catch {
          /* use catalog merge without discovery */
        }
      }
    }

    let cloudModelIds: string[] = [];
    const gx10Caps = await this.fetchGx10Capabilities();
    if (shouldOfferBaboCloudModels({ tier, hasCloudApi })) {
      cloudModelIds = await this.fetchCloudModelIds().catch(() => []);
    }

    this.defaultModelId.set(model);
    this.defaultModelLabel.set(this.labelFor(model));
    this.catalog.set(
      mergeModelCatalog({
        tier,
        hasCloudApi,
        defaultModelId: model,
        discoveredIds: discovered,
        cloudModelIds,
        includeProviderDefaults: tier === 'byok_cloud',
        providerId: matchCloudProvider(profile?.inference?.url ?? cfg.inferenceUrl ?? ''),
        hostedGx10Available: gx10Caps.available,
        hostedGx10Label: gx10Caps.label,
      }),
    );
  }

  private async refreshFromWebConfig(): Promise<void> {
    const tier: CapabilityTier = 'hosted_babo';
    this.inferenceTier.set(tier);
    const hasCloudApi = !!this.apiBase();

    let model = resolveBaboCloudModelId('');
    let cloudModelIds: string[] = [];
    const gx10Caps = await this.fetchGx10Capabilities();

    try {
      cloudModelIds = await this.fetchCloudModelIds();
      if (cloudModelIds.length) {
        const preferred = cloudModelIds.find((id) =>
          id.includes('qwen') || id.includes('gpt-4o-mini'),
        );
        model = resolveBaboCloudModelId(preferred ?? cloudModelIds[0]);
      }
    } catch {
      /* Babo Cloud list still available from static catalog */
    }

    this.defaultModelId.set(model);
    this.defaultModelLabel.set(this.labelFor(model));
    this.catalog.set(
      mergeModelCatalog({
        tier,
        hasCloudApi,
        defaultModelId: model,
        cloudModelIds,
        hostedGx10Available: gx10Caps.available,
        hostedGx10Label: gx10Caps.label,
      }),
    );
  }

  private async fetchGx10Capabilities(): Promise<{
    available: boolean;
    label?: string;
  }> {
    if (!this.apiBase()) return { available: false };
    try {
      const caps = await firstValueFrom(this.api.getPlatformCapabilities());
      return {
        available: !!caps.inference?.hostedGx10Available,
        label: caps.inference?.hostedGx10Label,
      };
    } catch {
      return { available: false };
    }
  }

  private async fetchCloudModelIds(): Promise<string[]> {
    const base = this.apiBase().replace(/\/+$/, '');
    const url = `${base}/inference/v1/models`;
    const payload = await firstValueFrom(this.http.get<unknown>(url));
    return parseOpenAiModelList(payload);
  }
}
