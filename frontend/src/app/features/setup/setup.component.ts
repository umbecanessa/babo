import {
  Component,
  OnInit,
  OnDestroy,
  signal,
  ViewChild,
  ElementRef,
  effect,
  computed,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router, ActivatedRoute, RouterLink } from '@angular/router';
import { firstValueFrom } from 'rxjs';
import { PlatformService } from '../../core/services/platform.service';
import { AuthService } from '../../core/services/auth.service';
import { ApiService } from '../../core/services/api.service';
import type {
  CapabilityProfile,
  CapabilityScan,
  CapabilityTier,
} from './capability-profile.model';
import {
  formatBackendReachabilityMessage,
  isBackendReachableStatus,
} from '../../core/backend-reachability.util';
import {
  BABO_HOSTED_MODEL_ID,
  CLOUD_PROVIDERS,
  baboCloudModelsForUser,
  resolveBaboCloudModelId,
  matchCloudProvider,
  stripInferenceV1Suffix,
} from './setup-inference.util';
import {
  BACKEND_CHOICES,
  BABO_CLOUD_BACKEND_URL,
  type BackendChoiceId,
  backendDisplayLabel,
  matchBackendChoice,
  normalizeNestjsUrl,
} from './setup-backend.util';
import {
  applyBaboCloudPlacements,
  usesBaboCloudRelay,
  usesHostedBaboCloud,
} from './setup-cloud.util';
import { ApiKeyService } from '../../core/services/api-key.service';
import { BillingService } from '../../core/services/billing.service';
import { AgentModelService } from '../../core/services/agent-model.service';
import { isPaidOrComp, CLOUD_BASIC_PRICE_AMOUNT } from '../../core/models/cloud-subscription.model';
import { ToastService } from '../../shared/toast/toast.service';
import { Day1CoachService } from '../../shared/onboarding/day1-coach.service';
import { AnalyticsService } from '../../core/services/analytics.service';
import { setupStepName } from '../../core/analytics/setup-steps';

interface SetupConfig {
  inferenceUrl: string;
  inferenceModel: string;
  inferenceApiKey: string;
  nestjsUrl: string;
  gpuWorkerUrl?: string;
  gpuWorkerSecret?: string;
}

interface ExpRow {
  label: string;
  value: string;
  ok: boolean;
}

@Component({
  selector: 'app-setup',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './setup.component.html',
  styleUrl: './setup.component.scss',
})
/** Screen: 0 welcome → 1 prepare → 2 device → 3 thinking → 4 extras → [5 placement] → 6 sign-in → 7 billing? → 8 ready → 9 name */
export class SetupComponent implements OnInit, OnDestroy {
  readonly cloudBasicPriceAmount = CLOUD_BASIC_PRICE_AMOUNT;
  readonly decisionSteps = [
    { id: 'thinking', label: 'Thinking' },
    { id: 'features', label: 'Features' },
    { id: 'placement', label: 'Account sync' },
    { id: 'signin', label: 'Sign in' },
  ];

  /** Babo Cloud inference only works through api.babo.agency — skip the placement step. */
  needsAccountServerStep = computed(() => this.brainTier() !== 'hosted_babo');

  visibleDecisionSteps = computed(() =>
    this.needsAccountServerStep()
      ? this.decisionSteps
      : this.decisionSteps.filter((s) => s.id !== 'placement'),
  );

  readonly brainCards: {
    tier: CapabilityTier;
    title: string;
    subtitle: string;
    glyph: string;
    gx10?: boolean;
  }[] = [
    {
      tier: 'hosted_babo',
      title: 'Babo Cloud',
      subtitle: 'Hosted models + account sync — your agents still run on this computer',
      glyph: '☁',
    },
    {
      tier: 'self_local',
      title: 'This computer',
      subtitle: 'Ollama on this PC',
      glyph: '◻',
    },
    {
      tier: 'self_lan',
      title: 'My server',
      subtitle: 'vLLM or OpenAI-compatible server on your LAN',
      glyph: '⎔',
    },
  ];

  readonly baboCloudModels = computed(() =>
    baboCloudModelsForUser({
      hostedGx10Available: this.platformCaps()?.inference?.hostedGx10Available,
      hostedGx10Label: this.platformCaps()?.inference?.hostedGx10Label,
    }),
  );

  step = signal(0);
  returningUser = signal(false);
  showByokPanel = signal(false);
  showLanSheet = signal(false);
  showVoiceSheet = signal(false);
  authEmail = '';
  authPassword = '';
  authDisplayName = '';
  authAccountMode = signal<'signin' | 'signup'>('signin');
  authError = signal<string | null>(null);
  signingIn = signal(false);
  private static readonly WIZARD_DRAFT_KEY = 'babo-setup-wizard-draft';
  prepareAutoAdvanced = false;
  brainUndoTier = signal<CapabilityTier | null>(null);
  agentName = '';
  provisioningCloud = signal(false);
  billingCheckoutLoading = signal(false);
  billingConfirming = signal(false);
  billingAwaitingPayment = signal(false);
  billingAlert = signal<{ kind: 'error' | 'warn'; text: string } | null>(null);
  private billingPollTimer: ReturnType<typeof setInterval> | null = null;
  private billingFocusHandler: (() => void) | null = null;
  private billingActivatedTracked = false;
  setupStage = signal<string>('idle');
  setupProgress = signal(0);
  setupMessage = signal('');
  setupError = signal<string | null>(null);
  venvReady = signal(false);
  elapsedTime = signal('0:00');
  showDetails = signal(false);
  logLines = signal<{ level: string; message: string }[]>([]);
  testing = signal(false);
  testResult = signal<{ ok: boolean; message: string; latency: number } | null>(null);
  /** Tier that `testResult` belongs to (avoid showing errors on the wrong card). */
  brainTestedTier = signal<CapabilityTier | null>(null);
  /** After "I already have an account", skip placement and land on sign-in once extras are done. */
  private preferSignInAfterConfig = false;
  /** Furthest step reached this run — blocks jumping past gaps in the wizard. */
  private highestStepReached = 0;
  private setupCompletedThisSession = false;
  launching = signal(false);
  launchMessage = signal('Starting agent runtime...');
  launchError = signal<string | null>(null);

  scanLoading = signal(false);
  scan = signal<CapabilityScan | null>(null);
  profile = signal<CapabilityProfile | null>(null);
  lanHost = '';
  /** SSH user for remote GPU scan (e.g. ubuntu). Host can be user@ip or ip + this field. */
  lanSshUser = '';
  lanSshPort = '';
  lanGpuSecret = '';
  lanProbing = signal(false);
  lanModelFitLoading = signal(false);
  showLanAdvanced = signal(false);

  ambientVisionOn = signal(false);
  codeSearchOn = signal(true);
  brainTier = signal<CapabilityTier>('byok_cloud');
  voiceTier = signal<CapabilityTier>('self_local');
  visualTier = signal<CapabilityTier>('off');
  cloudProviderId = signal('openrouter');
  platformCaps = signal<import('../../core/models/platform-capabilities.model').PlatformCapabilities | null>(null);
  isPaidOrComp = isPaidOrComp;
  recommendedBrainTier: CapabilityTier = 'hosted_babo';
  savingCapabilities = signal(false);
  visionPrefetchActive = signal(false);
  visionPrefetchMessage = signal('');
  private visionPrefetchListener: ((data: unknown) => void) | null = null;

  showDecisionDots = computed(() => {
    const s = this.step();
    return s >= 3 && s <= 6;
  });

  decisionStepIndex = computed(() => {
    const s = this.step();
    if (s === 3) return 0;
    if (s === 4) return 1;
    if (this.needsAccountServerStep()) {
      if (s === 5) return 2;
      if (s === 6) return 3;
    } else if (s === 6) {
      return 2;
    }
    return -1;
  });

  localModelFit = computed(() => this.scan()?.modelFit?.local ?? null);
  lanModelFit = computed(() => this.scan()?.modelFit?.lan ?? null);

  deviceTagline = computed(() => {
    const fit = this.localModelFit();
    if (fit?.localViable && fit.recommendations[0]) {
      return `This PC can run ${fit.recommendations[0].displayName} locally.`;
    }
    if (fit && !fit.localViable) {
      return 'Local chat models are a tight fit — Babo Cloud is recommended.';
    }
    const vram = this.scan()?.device.vramGb ?? 0;
    if (vram >= 6) {
      return 'Good for running helpers on this computer.';
    }
    return 'Best with Babo Cloud or a network server for heavy models.';
  });

  localVisionReady = computed(() => (this.scan()?.device.vramGb ?? 0) >= 6);

  lanInferenceFound = computed(() =>
    !!this.scan()?.lan.some((s) => s.kind === 'inference' && s.healthy && s.port === 8000),
  );

  /** Screen-awareness placement options depend on where chat runs. */
  visualPlacementOptions = computed(() => {
    const brain = this.brainTier();
    const opts: { tier: CapabilityTier; label: string; disabled: boolean }[] = [];

    const add = (tier: CapabilityTier, label: string, disabled: boolean) => {
      if (!opts.some((o) => o.tier === tier)) {
        opts.push({ tier, label, disabled });
      }
    };

    if (brain === 'hosted_babo') {
      add('hosted_babo', 'Babo Cloud', false);
      if (this.hasLanVision()) add('self_lan', 'Your LAN server', false);
      if (this.canLocalVision()) add('self_local', 'This PC (Moondream)', false);
    } else if (brain === 'self_lan') {
      if (this.hasLanVision()) add('self_lan', 'Same LAN server', false);
      add('hosted_babo', 'Babo Cloud', false);
      if (this.canLocalVision()) add('self_local', 'This PC', false);
    } else {
      if (this.canLocalVision()) add('self_local', 'This PC', false);
      if (this.hasLanVision()) add('self_lan', 'LAN server', false);
      add('hosted_babo', 'Babo Cloud', false);
    }

    return opts;
  });

  readySummary = computed(() => {
    const p = this.profile();
    if (!p) return [];
    const thinking =
      p.inference.tier === 'hosted_babo'
        ? 'Babo Cloud'
        : p.inference.tier === 'byok_cloud'
          ? 'Your API key'
          : p.inference.model || tierLabel(p.inference.tier);
    const extras = `Code search ${this.codeSearchOn() ? 'on' : 'off'} · Screen ${this.ambientVisionOn() ? 'on' : 'off'}`;
    const account = this.auth.isAuthenticated()
      ? `${this.backendSummaryLabel()} (signed in)`
      : this.backendSummaryLabel();
    return [
      { icon: '🧠', label: 'Thinking', value: thinking },
      { icon: '✦', label: 'Extras', value: extras },
      { icon: '☁', label: 'Account', value: account },
    ];
  });

  /** Where models run (step 3) — separate from NestJS account (steps 5–6). */
  inferenceRunLocation = computed(() => {
    switch (this.brainTier()) {
      case 'hosted_babo':
        return 'Babo Cloud';
      case 'byok_cloud':
        return 'your API provider';
      case 'self_local':
        return 'this computer';
      case 'self_lan':
        return 'your LAN server';
      default:
        return 'your chosen provider';
    }
  });

  placementStepLead = computed(() => {
    const tier = this.brainTier();
    if (tier === 'byok_cloud') {
      return (
        'Your API keys can relay through Babo Cloud (recommended) or a NestJS server you run. ' +
        'Pick where your Babo account syncs — agents still run locally.'
      );
    }
    return (
      `Pick where your Babo account syncs. Chat runs on ${this.inferenceRunLocation()}; agents stay on your devices.`
    );
  });

  signInStepLead = computed(() => {
    const server = this.backendSummaryLabel();
    if (this.authAccountMode() === 'signup') {
      return `Create your account on ${server}. You’ll finish setup right after.`;
    }
    return `Sign in on ${server} — required for agents and sync.`;
  });

  backendChoice = signal<BackendChoiceId>('babo_cloud');
  backendTesting = signal(false);
  backendTestResult = signal<{ ok: boolean; message: string; latency: number } | null>(null);
  backendSaveError = signal<string | null>(null);
  savingBackend = signal(false);

  readonly cloudProviders = CLOUD_PROVIDERS;
  readonly backendChoices = BACKEND_CHOICES;

  voiceOptions = [
    { tier: 'self_local' as CapabilityTier, label: 'This computer', shortLabel: 'This PC' },
    { tier: 'self_lan' as CapabilityTier, label: 'My server', shortLabel: 'My server' },
    { tier: 'off' as CapabilityTier, label: 'Off', shortLabel: 'Off' },
  ];

  lanServices = computed(() => this.scan()?.lan ?? []);

  visibleBrainCards = computed(() => {
    const caps = this.platformCaps();
    const cards = [...this.brainCards];
    if (caps?.inference?.hostedGx10Available) {
      const label = caps.inference.hostedGx10Label || 'Babo Brain (GX10)';
      return [
        cards[0],
        {
          tier: 'hosted_babo' as CapabilityTier,
          title: label,
          subtitle: 'Private GX10 inference — complimentary access',
          glyph: '🧠',
          gx10: true,
        },
        ...cards.slice(1),
      ];
    }
    return cards;
  });

  config: SetupConfig = {
    inferenceUrl: 'https://openrouter.ai/api',
    inferenceModel: 'openai/gpt-4o-mini',
    inferenceApiKey: '',
    nestjsUrl: 'https://api.babo.agency',
  };

  @ViewChild('logArea') private logArea?: ElementRef<HTMLDivElement>;

  private progressListener: ((data: unknown) => void) | null = null;
  private logListener: ((data: unknown) => void) | null = null;
  private elapsedTimer: ReturnType<typeof setInterval> | null = null;

  constructor(
    private router: Router,
    private route: ActivatedRoute,
    public platform: PlatformService,
    public auth: AuthService,
    private api: ApiService,
    private apiKeys: ApiKeyService,
    public billing: BillingService,
    private agentModels: AgentModelService,
    private toast: ToastService,
    private day1Coach: Day1CoachService,
    private analytics: AnalyticsService,
  ) {
    effect(() => {
      this.logLines();
      setTimeout(() => {
        const el = this.logArea?.nativeElement;
        if (el) el.scrollTop = el.scrollHeight;
      });
    });
  }

  async ngOnInit(): Promise<void> {
    if (!this.platform.isElectron) {
      this.router.navigate(['/dashboard']);
      return;
    }

    const nls = (window as any).nls;
    try {
      const cfg = await nls.config.get();
      this.config = {
        ...this.config,
        ...cfg,
        inferenceUrl: stripInferenceV1Suffix(
          cfg.inferenceUrl || cfg.vllmUrl || this.config.inferenceUrl,
        ),
        inferenceModel: cfg.inferenceModel || cfg.hfModel || this.config.inferenceModel,
        inferenceApiKey: cfg.inferenceApiKey || '',
        nestjsUrl: normalizeNestjsUrl(cfg.nestjsUrl || this.config.nestjsUrl),
        gpuWorkerSecret: cfg.gpuWorkerSecret || '',
      };
      this.lanGpuSecret = this.config.gpuWorkerSecret || '';
      this.backendChoice.set(matchBackendChoice(this.config.nestjsUrl));
      if (cfg.capabilityProfile && cfg.setupComplete) {
        this.profile.set(cfg.capabilityProfile);
        this.applyProfileToUi(cfg.capabilityProfile);
      }
    } catch { /* ignore */ }

    let setupComplete = false;
    try {
      const check = await nls.setup.check();
      setupComplete = !!check.setupComplete;
      this.venvReady.set(!!check.venvReady);
      if (check.venvReady) {
        this.setupStage.set('ready');
      }
      if (check.setupComplete) {
        this.returningUser.set(true);
      }
    } catch { /* ignore */ }

    if (this.auth.isAuthenticated()) {
      this.returningUser.set(true);
    }

    if (!setupComplete) {
      this.restoreWizardDraft();
    }

    const billingReturn = this.route.snapshot.queryParamMap.get('billing');
    if (billingReturn === 'success' || billingReturn === 'canceled') {
      void this.handleBillingReturn(billingReturn);
    } else if (!setupComplete && this.step() >= 8 && this.auth.isAuthenticated()) {
      void this.enforceBillingStepIfNeeded();
    }

    this.progressListener = (data: any) => {
      this.setupStage.set(data.stage);
      this.setupProgress.set(data.progress);
      this.setupMessage.set(data.message);
      if (data.error) this.setupError.set(data.error);
      if (data.stage === 'ready') {
        this.venvReady.set(true);
        this.stopElapsedTimer();
      } else if (data.stage === 'error') {
        this.stopElapsedTimer();
      } else if (
        data.stage === 'checking' ||
        data.stage === 'creating-venv' ||
        data.stage === 'installing'
      ) {
        this.ensureElapsedTimerForInstall();
      }
    };
    nls.on('setup:progress', this.progressListener);

    this.logListener = (data: any) => {
      const msg = (data.message || '').trim();
      if (!msg) return;
      this.logLines.update((lines) => {
        const updated = [...lines, { level: data.level, message: msg }];
        return updated.length > 200 ? updated.slice(-200) : updated;
      });
    };
    nls.on('setup:log', this.logListener);

    this.visionPrefetchListener = (data: any) => {
      const msg = (data?.message || '').trim();
      if (msg) this.visionPrefetchMessage.set(msg);
      if (typeof data?.progress === 'number' && data.progress >= 100) {
        this.visionPrefetchActive.set(false);
      } else if (msg) {
        this.visionPrefetchActive.set(true);
      }
    };
    nls.on('vision:prefetch-progress', this.visionPrefetchListener);

    this.billingFocusHandler = () => {
      if (this.step() === 7 && this.billingAwaitingPayment()) {
        void this.checkBillingActivation(true);
      }
    };
    window.addEventListener('focus', this.billingFocusHandler);

    this.analytics.captureAttributionFromUrl();
    await this.analytics.claimAttributionHandoff();
    this.analytics.track('setup_started', {
      returning_user: this.returningUser(),
      venv_ready: this.venvReady(),
      restored_step: this.step(),
      attribution_ref: this.analytics.getAttributionRef(),
    });
    this.trackSetupStep(this.step());
  }

  ngOnDestroy(): void {
    if (!this.setupCompletedThisSession && this.highestStepReached < 9) {
      this.analytics.track('setup_abandoned', {
        step: this.step(),
        step_name: setupStepName(this.step()),
        highest_step_reached: this.highestStepReached,
        highest_step_name: setupStepName(this.highestStepReached),
      });
    }
    this.stopBillingSubscriptionPoll();
    if (this.billingFocusHandler) {
      window.removeEventListener('focus', this.billingFocusHandler);
      this.billingFocusHandler = null;
    }
    this.stopElapsedTimer();
    const nls = (window as any).nls;
    if (this.progressListener) {
      nls?.removeListener?.('setup:progress', this.progressListener);
    }
    if (this.logListener) {
      nls?.removeListener?.('setup:log', this.logListener);
    }
    if (this.visionPrefetchListener) {
      nls?.removeListener?.('vision:prefetch-progress', this.visionPrefetchListener);
    }
  }

  private shouldPrefetchLocalVision(profile: CapabilityProfile): boolean {
    return (
      this.ambientVisionOn() &&
      profile.visualCortex.strategy === 'dedicated_vlm_local'
    );
  }

  private startVisionPrefetchInBackground(): void {
    if (!this.venvReady()) return;
    this.visionPrefetchActive.set(true);
    this.visionPrefetchMessage.set('Downloading screen awareness model…');
    void this.nls()
      .capabilities.prefetchVision()
      .catch(() => {
        this.visionPrefetchMessage.set('Will download on first use');
        setTimeout(() => this.visionPrefetchActive.set(false), 4000);
      });
  }

  private nls(): any {
    return (window as any).nls;
  }

  lanKindLabel(kind: string, port?: number): string {
    switch (kind) {
      case 'inference':
        return port === 11434 ? 'Ollama' : 'Chat model';
      case 'vision':
        return 'Vision';
      case 'transcribe':
        return 'Voice';
      default:
        return kind;
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

  onBrainServerUrlEdit(url: string): void {
    this.setBrainServerUrl(url);
    this.testResult.set(null);
    this.brainTestedTier.set(null);
  }

  brainModelLabel(): string {
    const tier = this.profile()?.inference.tier;
    const raw = this.profile()?.inference.model ?? this.config.inferenceModel ?? '';
    if (tier === 'hosted_babo') {
      return resolveBaboCloudModelId(raw);
    }
    return raw;
  }

  baboCloudModelActive(modelId: string): boolean {
    return this.brainModelLabel() === modelId;
  }

  brainCardTestLine(tier: CapabilityTier): string {
    const tr = this.testResult();
    if (!tr) return '';
    if (!tr.ok) return tr.message;
    if (tier === 'hosted_babo') {
      const m = this.profile()?.inference.model;
      const id = resolveBaboCloudModelId(m);
      const label = this.baboCloudModels().find((x) => x.id === id)?.label ?? m;
      return label ? `✓ ${label} (via Babo Cloud)` : '✓ Babo Cloud';
    }
    if (tier === 'self_local' && tr.message) {
      return `✓ ${tr.message}`;
    }
    const model = this.brainModelLabel();
    return model ? `✓ ${model}` : '✓ Connected';
  }

  selectBaboCloudModel(modelId: string): void {
    const p = this.profile();
    if (!p) return;
    p.inference.model = modelId;
    this.config.inferenceModel = modelId;
    this.profile.set({ ...p });
    this.testResult.set(null);
    this.brainTestedTier.set(null);
  }

  setVisualPlacement(tier: CapabilityTier): void {
    if (tier === 'hosted_babo') this.setVisualHostedBabo();
    else if (tier === 'self_lan') this.setVisualLan();
    else if (tier === 'self_local') this.setVisualLocal();
  }

  setVisualHostedBabo(): void {
    const p = this.profile();
    if (!p) return;
    p.visualCortex = {
      tier: 'hosted_babo',
      strategy: 'dedicated_vlm_lan',
      url: '',
      reason: 'Screen awareness via Babo Cloud GPU relay',
    };
    this.visualTier.set('hosted_babo');
    this.profile.set({ ...p });
  }

  async startSetup(): Promise<void> {
    this.analytics.track('setup_prepare_started');
    this.setupStage.set('checking');
    this.setupError.set(null);
    this.logLines.set([]);
    this.ensureElapsedTimerForInstall();
    try {
      await this.nls().setup.start();
      this.setupStage.set('ready');
      this.venvReady.set(true);
      this.analytics.track('setup_prepare_completed');
      this.maybeAutoAdvanceFromPrepare();
    } catch (err: any) {
      this.setupStage.set('error');
      this.setupError.set(err?.message || 'Setup failed');
      this.analytics.track('setup_prepare_failed', {
        error: (err?.message || 'Setup failed').slice(0, 120),
      });
    } finally {
      this.stopElapsedTimer();
    }
  }

  retrySetup(): void {
    this.setupStage.set('idle');
    this.setupError.set(null);
  }

  goToStep(target: number): void {
    if (target === 5 && !this.needsAccountServerStep()) {
      target = 6;
    }
    if (!this.canEnterStep(target)) return;
    if (this.step() === 7 && target !== 7) {
      this.stopBillingSubscriptionPoll();
      this.billingAwaitingPayment.set(false);
    }
    this.step.set(target);
    this.highestStepReached = Math.max(this.highestStepReached, target);
    this.persistWizardDraft();
    this.trackSetupStep(target);
    if (target === 7) {
      void this.onEnterBillingStep();
    }
  }

  private async onEnterBillingStep(): Promise<void> {
    if (!this.auth.isAuthenticated()) return;
    await this.loadPlatformCaps();
    const active = await this.checkBillingActivation(false);
    if (active) return;
    if (this.billingAwaitingPayment()) {
      this.startBillingSubscriptionPoll();
    }
  }

  persistWizardDraft(): void {
    try {
      const p = this.profile();
      sessionStorage.setItem(
        SetupComponent.WIZARD_DRAFT_KEY,
        JSON.stringify({
          step: this.step(),
          highestStepReached: this.highestStepReached,
          nestjsUrl: this.config.nestjsUrl,
          backendChoice: this.backendChoice(),
          authEmail: this.authEmail,
          authAccountMode: this.authAccountMode(),
          capabilityProfile: p,
          brainTier: this.brainTier(),
          ambientVisionOn: this.ambientVisionOn(),
          codeSearchOn: this.codeSearchOn(),
        }),
      );
    } catch { /* ignore */ }
  }

  private restoreWizardDraft(): void {
    try {
      const raw = sessionStorage.getItem(SetupComponent.WIZARD_DRAFT_KEY);
      if (!raw) return;
      const d = JSON.parse(raw) as {
        step?: number;
        highestStepReached?: number;
        nestjsUrl?: string;
        backendChoice?: BackendChoiceId;
        authEmail?: string;
        authAccountMode?: 'signin' | 'signup';
        capabilityProfile?: CapabilityProfile;
        brainTier?: CapabilityTier;
        ambientVisionOn?: boolean;
        codeSearchOn?: boolean;
      };
      if (d.nestjsUrl) {
        this.config.nestjsUrl = normalizeNestjsUrl(d.nestjsUrl);
        this.backendChoice.set(matchBackendChoice(this.config.nestjsUrl));
      }
      if (d.authEmail) this.authEmail = d.authEmail;
      if (d.authAccountMode) this.authAccountMode.set(d.authAccountMode);
      if (d.capabilityProfile) {
        this.profile.set(d.capabilityProfile);
        this.applyProfileToUi(d.capabilityProfile);
      }
      if (d.brainTier) this.brainTier.set(d.brainTier);
      if (typeof d.ambientVisionOn === 'boolean') {
        this.ambientVisionOn.set(d.ambientVisionOn);
      }
      if (typeof d.codeSearchOn === 'boolean') {
        this.codeSearchOn.set(d.codeSearchOn);
      }
      const target = Math.min(Math.max(d.step ?? 0, 1), 9);
      this.highestStepReached = d.highestStepReached ?? target;
      if (this.canEnterStep(target)) {
        this.step.set(target);
      } else if (target === 5 && !this.needsAccountServerStep()) {
        this.step.set(6);
      }
    } catch { /* ignore */ }
  }

  private clearWizardDraft(): void {
    try {
      sessionStorage.removeItem(SetupComponent.WIZARD_DRAFT_KEY);
    } catch { /* ignore */ }
  }

  setAuthAccountMode(mode: 'signin' | 'signup'): void {
    this.authAccountMode.set(mode);
    this.authError.set(null);
    this.persistWizardDraft();
  }

  canEnterStep(target: number): boolean {
    if (target <= 1) return true;
    if (target >= 2 && !this.venvReady()) return false;
    if (target >= 3 && !this.profile()) return false;
    if (target === 5 && !this.needsAccountServerStep()) return false;
    if (target >= 7 && !this.auth.isAuthenticated()) return false;
    if (target >= 8 && this.requiresSetupSubscription()) return false;
    return true;
  }

  nextStep(): void {
    const n = Math.min(this.step() + 1, 9);
    if (!this.canEnterStep(n)) return;
    this.goToStep(n);
  }

  async prevStep(): Promise<void> {
    let n = Math.max(this.step() - 1, 0);
    if (n === 5 && !this.needsAccountServerStep()) {
      n = 4;
    }
    this.goToStep(n);
    if (n === 2 && !this.scan() && !this.scanLoading()) {
      await this.runDeviceScan();
    }
  }

  /** Full first-run path: prepare → … → sign-in → billing (if Babo Cloud) → ready → name */
  continueFromWelcome(): void {
    this.analytics.track('setup_welcome_continue', { path: 'new' });
    this.goToStep(1);
    if (this.setupStage() === 'idle') {
      void this.startSetup();
    } else if (this.setupStage() === 'ready') {
      void this.goToScan();
    }
  }

  /** Same setup as Continue; after features, jump to sign-in (placement still suggested). */
  beginSetupWithExistingAccount(): void {
    this.analytics.track('setup_welcome_continue', { path: 'existing_account' });
    this.preferSignInAfterConfig = true;
    this.continueFromWelcome();
  }

  isBackendSuggested(id: BackendChoiceId): boolean {
    const tier = this.brainTier();
    if (tier === 'hosted_babo') return id === 'babo_cloud';
    if (tier === 'self_local') return id === 'local';
    if (tier === 'byok_cloud') return id === 'babo_cloud';
    return id === 'babo_cloud';
  }

  suggestBackendFromThinking(): void {
    const tier = this.brainTier();
    if (tier === 'hosted_babo' || tier === 'byok_cloud') {
      this.applyBaboCloudBackend();
    } else if (tier === 'self_local') {
      this.selectBackendChoice('local');
    }
  }

  applyBaboCloudBackend(): void {
    this.selectBackendChoice('babo_cloud');
    this.config.nestjsUrl = BABO_CLOUD_BACKEND_URL;
    this.backendTestResult.set(null);
    this.backendSaveError.set(null);
  }

  async persistBackendUrl(): Promise<void> {
    this.config.nestjsUrl = normalizeNestjsUrl(this.config.nestjsUrl);
    await this.nls().config.set({ nestjsUrl: this.config.nestjsUrl });
    await this.api.whenReady();
  }

  private maybeAutoAdvanceFromPrepare(): void {
    if (this.prepareAutoAdvanced || this.step() !== 1) return;
    this.prepareAutoAdvanced = true;
    setTimeout(() => {
      if (this.setupStage() === 'ready' && this.step() === 1) {
        void this.goToScan();
      }
    }, 1200);
  }

  isBrainSuggested(tier: CapabilityTier): boolean {
    return tier === this.recommendedBrainTier;
  }

  isBrainCardActive(card: { tier: CapabilityTier; gx10?: boolean }): boolean {
    if (this.brainTier() !== card.tier) return false;
    const resolved = resolveBaboCloudModelId(this.profile()?.inference.model);
    if (card.gx10) return resolved === BABO_HOSTED_MODEL_ID;
    return resolved !== BABO_HOSTED_MODEL_ID;
  }

  async selectBrainCard(tier: CapabilityTier, gx10 = false): Promise<void> {
    const previous = this.brainTier();
    this.showByokPanel.set(false);
    this.brainUndoTier.set(null);
    this.testResult.set(null);
    this.brainTestedTier.set(null);
    this.onBrainTierChange(tier);
    if (gx10) {
      this.selectBaboCloudModel(BABO_HOSTED_MODEL_ID);
    }

    if (tier === 'hosted_babo') {
      if (this.auth.isAuthenticated()) {
        await this.testBrain();
      } else {
        this.testResult.set({
          ok: true,
          message: 'Sign in on the next step to connect',
          latency: 0,
        });
        this.brainTestedTier.set('hosted_babo');
      }
    } else if (tier === 'byok_cloud' && this.config.inferenceApiKey.trim()) {
      await this.testBrain();
    }

    if (
      this.isBrainSuggested(tier) &&
      (this.testResult()?.ok || tier === 'hosted_babo')
    ) {
      this.brainUndoTier.set(previous);
      this.toast.show(
        tier === 'hosted_babo' ? 'Using Babo Cloud' : `Using ${this.brainCardTitle(tier)}`,
        'info',
        8000,
      );
    }
    this.analytics.track('setup_tier_selected', {
      tier,
      gx10,
    });
  }

  undoBrainSelection(): void {
    const prev = this.brainUndoTier();
    if (!prev) return;
    this.brainUndoTier.set(null);
    void this.selectBrainCard(prev);
  }

  brainCardTitle(tier: CapabilityTier): string {
    return this.brainCards.find((c) => c.tier === tier)?.title ?? tier;
  }

  useOwnApiKey(): void {
    this.showByokPanel.set(true);
    this.onBrainTierChange('byok_cloud');
  }

  switchToByokFromBilling(): void {
    this.billingAlert.set(null);
    this.useOwnApiKey();
    this.goToStep(3);
    this.persistWizardDraft();
  }

  private async syncByokKeyToCloud(): Promise<void> {
    const key = this.config.inferenceApiKey?.trim();
    if (!key || !this.auth.isAuthenticated()) return;
    await firstValueFrom(
      this.api.setCloudInferenceProviderKey(this.cloudProviderId(), key),
    );
  }

  canContinueThinking(): boolean {
    const tier = this.brainTier();
    if (tier === 'hosted_babo') return true;
    if (tier === 'byok_cloud') {
      return !!this.config.inferenceApiKey.trim() && this.testResult()?.ok === true;
    }
    const url = stripInferenceV1Suffix(this.profile()?.inference.url ?? '');
    if (!url) return false;
    if (tier === 'self_lan' && url.includes('11434')) return false;
    return this.testResult()?.ok === true;
  }

  brainTestShowsOnCard(tier: CapabilityTier): boolean {
    return this.brainTier() === tier && this.brainTestedTier() === tier && !!this.testResult();
  }

  continueFromThinking(): void {
    if (!this.canContinueThinking()) return;
    this.nextStep();
  }

  openVoiceSheet(): void {
    this.showVoiceSheet.set(true);
  }

  closeVoiceSheet(): void {
    this.showVoiceSheet.set(false);
  }

  voiceSummary(): string {
    const opt = this.voiceOptions.find((o) => o.tier === this.voiceTier());
    return opt?.label ?? 'Off';
  }

  async goToScan(): Promise<void> {
    if (!this.venvReady()) {
      this.setupError.set('Wait for Python setup to finish before continuing.');
      return;
    }
    this.goToStep(2);
    await this.runDeviceScan();
  }

  async runDeviceScan(): Promise<void> {
    this.scanLoading.set(true);
    try {
      const scan = await this.nls().capabilities.scanDevice();
      const prev = this.scan();
      this.scan.set({
        ...scan,
        lan: [],
        modelFit: {
          local: scan.modelFit?.local ?? prev?.modelFit?.local,
          lan: prev?.modelFit?.lan,
        },
      });
      if (scan.modelFit?.local?.localViable) {
        this.recommendedBrainTier = 'self_local';
      } else if (scan.modelFit?.local && !scan.modelFit.local.localViable) {
        this.recommendedBrainTier = 'hosted_babo';
      }
      const vram = scan.device?.vramGb ?? 0;
      this.analytics.track('setup_device_scanned', {
        ok: true,
        vram_bucket: vram >= 12 ? '12gb+' : vram >= 6 ? '6gb+' : 'under_6gb',
        recommended_tier: this.recommendedBrainTier,
      });
    } catch (err: any) {
      this.setupError.set(err?.message || 'Scan failed');
      this.analytics.track('setup_device_scanned', {
        ok: false,
        error: (err?.message || 'Scan failed').slice(0, 120),
      });
    } finally {
      this.scanLoading.set(false);
    }
  }

  resolvedLanSshUser(): string | undefined {
    const host = this.lanHost.trim();
    if (host.includes('@')) {
      return host.split('@')[0]?.trim() || undefined;
    }
    const u = this.lanSshUser.trim();
    return u || undefined;
  }

  lanSshOptions(): { user?: string; port?: number } | undefined {
    const user = this.resolvedLanSshUser();
    if (!user) return undefined;
    const port = parseInt(this.lanSshPort.trim(), 10);
    return {
      user,
      port: Number.isFinite(port) && port > 0 ? port : undefined,
    };
  }

  async runLanProbe(): Promise<void> {
    if (!this.lanHost.trim()) return;
    this.lanProbing.set(true);
    const secret = this.lanGpuSecret.trim();
    try {
      const prev = this.scan();
      const full = (await this.nls().capabilities.probeLan(
        this.lanHost.trim(),
        secret,
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
      if (
        full.lan.some((s) => s.kind === 'inference' && s.healthy && s.port === 8000)
      ) {
        this.recommendedBrainTier = 'self_lan';
      }
      if (full.modelFit?.lan?.localViable) {
        this.recommendedBrainTier = 'self_lan';
      }
      if (secret) {
        this.config.gpuWorkerSecret = secret;
        await this.nls().config.set({ gpuWorkerSecret: secret });
      }
    } finally {
      this.lanProbing.set(false);
    }
  }

  async runLanModelFitOnly(): Promise<void> {
    if (!this.lanHost.trim() || !this.resolvedLanSshUser()) return;
    this.lanModelFitLoading.set(true);
    try {
      const snap = await this.nls().capabilities.modelFitRemote(
        this.lanHost.trim(),
        this.lanSshOptions(),
      );
      const prev = this.scan();
      if (prev) {
        this.scan.set({
          ...prev,
          modelFit: { ...prev.modelFit, lan: snap },
        });
      } else {
        this.scan.set({
          scannedAt: new Date().toISOString(),
          device: {
            platform: 'win32',
            ramGb: 0,
            vramGb: snap.vramGb,
            gpuName: snap.gpuName,
            hasCuda: true,
            hasMps: false,
            hasMlxVlm: false,
          },
          lan: [],
          modelFit: { lan: snap },
        });
      }
      if (snap?.localViable) {
        this.recommendedBrainTier = 'self_lan';
      }
    } catch (err: any) {
      this.setupError.set(err?.message || 'LAN model scan failed');
    } finally {
      this.lanModelFitLoading.set(false);
    }
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

  async applyRecommendationsAndContinue(): Promise<void> {
    const s = this.scan();
    if (!s) return;
    const gpuSecret = this.lanGpuSecret.trim() || this.config.gpuWorkerSecret || '';
    const recommended = await this.nls().capabilities.recommend(s, gpuSecret);
    this.profile.set(recommended);
    this.applyProfileToUi(recommended);
    if (recommended.inference.tier === 'self_lan') {
      this.showLanAdvanced.set(true);
    }
    this.nextStep();
  }

  applyProfileToUi(p: CapabilityProfile): void {
    if (p.inference.tier === 'hosted_babo') {
      p.inference.model = resolveBaboCloudModelId(p.inference.model);
      this.config.inferenceModel = p.inference.model;
    }
    this.brainTier.set(p.inference.tier);
    this.recommendedBrainTier = p.inference.tier;
    this.voiceTier.set(p.transcribe.tier);
    if (p.inference.url) {
      p.inference.url = stripInferenceV1Suffix(p.inference.url);
    }
    this.cloudProviderId.set(matchCloudProvider(p.inference.url ?? ''));
    const vcOn =
      p.visualCortex.tier !== 'off' &&
      (p.visualCortex.strategy ?? 'off') !== 'off';
    this.ambientVisionOn.set(vcOn);
    this.visualTier.set(vcOn ? p.visualCortex.tier : 'off');
    this.codeSearchOn.set(p.embeddings.tier !== 'off');
    this.syncInferenceLegacy();
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
        const cur = stripInferenceV1Suffix(p.inference.url || '');
        const looksLocalOllama = !cur || cur.includes('11434');
        p.inference.url = looksLocalOllama
          ? `http://${this.lanHost || '192.168.1.50'}:8000`
          : cur;
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
      this.applyBaboCloudBackend();
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
      const first = this.visualPlacementOptions().find((o) => !o.disabled);
      if (first) {
        this.setVisualPlacement(first.tier);
      } else if (p.inference.tier === 'hosted_babo') {
        this.setVisualHostedBabo();
      } else if (this.hasLanVision()) {
        this.setVisualLan();
      } else if (this.canLocalVision()) {
        this.setVisualLocal();
      }
    }
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
      url: v?.url ?? `http://${this.lanHost || '192.168.1.50'}:8443`,
      secret: p.visualCortex.secret ?? this.config.gpuWorkerSecret,
    };
    this.visualTier.set('self_lan');
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
    if (!p?.inference.url) return;
    this.config.inferenceUrl = stripInferenceV1Suffix(p.inference.url);
    if (p.inference.model) this.config.inferenceModel = p.inference.model;
  }

  async testBrain(): Promise<void> {
    const p = this.profile();
    if (!p) return;
    let url = stripInferenceV1Suffix(p.inference.url || this.config.inferenceUrl);
    let authToken: string | undefined =
      this.config.inferenceApiKey?.trim() || undefined;

    if (p.inference.tier === 'byok_cloud') {
      if (this.auth.isAuthenticated()) {
        try {
          await this.syncByokKeyToCloud();
        } catch (err: any) {
          this.testResult.set({
            ok: false,
            message: err?.error?.message || err?.message || 'Could not save API key',
            latency: 0,
          });
          this.brainTestedTier.set('byok_cloud');
          return;
        }
        const base = this.config.nestjsUrl.replace(/\/+$/, '');
        const apiBase = base.endsWith('/api') ? base : `${base}/api`;
        url = `${apiBase}/inference`;
        authToken = this.auth.getAccessToken() ?? undefined;
      } else {
        const prov = CLOUD_PROVIDERS.find((x) => x.id === this.cloudProviderId());
        if (prov) {
          url = stripInferenceV1Suffix(prov.baseUrl);
        }
      }
    } else if (p.inference.tier === 'hosted_babo') {
      const base = this.config.nestjsUrl.replace(/\/+$/, '');
      const apiBase = base.endsWith('/api') ? base : `${base}/api`;
      url = `${apiBase}/inference`;
      authToken = this.auth.getAccessToken() ?? undefined;
      if (!authToken) {
        this.testResult.set({
          ok: false,
          message: 'Sign in after setup to verify Babo Cloud',
          latency: 0,
        });
        return;
      }
      await this.billing.refresh();
      if (this.requiresSetupSubscription()) {
        this.testResult.set({
          ok: true,
          message: 'Subscribe on the next step to activate Babo Cloud models',
          latency: 0,
        });
        this.brainTestedTier.set('hosted_babo');
        return;
      }
    }

    if (!url) {
      this.testResult.set({ ok: false, message: 'Enter a server address first', latency: 0 });
      return;
    }
    this.testing.set(true);
    this.testResult.set(null);
    this.brainTestedTier.set(null);
    try {
      const result = await this.nls().capabilities.testInference(url, authToken);
      this.testResult.set(result);
      this.brainTestedTier.set(p.inference.tier);
      if (result.ok && p) {
        // Babo Cloud test hits OpenRouter /models (huge catalog) — never adopt models[0].
        if (
          result.models?.length &&
          p.inference.tier !== 'hosted_babo'
        ) {
          p.inference.model = result.models[0];
        } else if (p.inference.tier === 'hosted_babo') {
          p.inference.model = resolveBaboCloudModelId(p.inference.model);
          this.config.inferenceModel = p.inference.model;
        }
        p.inference.url = url;
        this.syncInferenceLegacy();
        this.profile.set({ ...p });
      }
    } catch (err: any) {
      this.testResult.set({ ok: false, message: err?.message || 'Test failed', latency: 0 });
      this.brainTestedTier.set(p.inference.tier);
    }
    const result = this.testResult();
    if (result) {
      this.analytics.track('setup_inference_test', {
        tier: p.inference.tier,
        ok: result.ok,
        latency_ms: result.latency,
      });
    }
    this.testing.set(false);
  }

  selectBackendChoice(id: BackendChoiceId): void {
    this.backendChoice.set(id);
    this.backendTestResult.set(null);
    this.backendSaveError.set(null);
    const choice = BACKEND_CHOICES.find((c) => c.id === id);
    if (id !== 'custom' && choice?.url) {
      this.config.nestjsUrl = normalizeNestjsUrl(choice.url);
    }
  }

  setCustomBackendUrl(url: string): void {
    this.config.nestjsUrl = normalizeNestjsUrl(url);
    this.backendTestResult.set(null);
    this.backendSaveError.set(null);
  }

  backendSummaryLabel(): string {
    return backendDisplayLabel(this.config.nestjsUrl, this.backendChoice());
  }

  canContinueBackend(): boolean {
    const url = normalizeNestjsUrl(this.config.nestjsUrl);
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
    const url = normalizeNestjsUrl(this.config.nestjsUrl);
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
      const ok = !!result.ok && isBackendReachableStatus(result.statusCode);
      this.backendTestResult.set({
        ok,
        message: formatBackendReachabilityMessage(
          ok,
          result.latency,
          result.statusCode,
          result.message,
        ),
        latency: result.latency,
      });
    } catch (err: any) {
      this.backendTestResult.set({
        ok: false,
        message: err?.message || 'Test failed',
        latency: 0,
      });
    }
    this.backendTesting.set(false);
  }

  async saveBackendPlacementAndContinue(): Promise<void> {
    if (!this.venvReady()) {
      this.backendSaveError.set(
        'Python is still installing. Finish the prepare step first (or wait if you skipped ahead).',
      );
      return;
    }
    if (!this.canContinueBackend()) {
      this.backendSaveError.set('Choose where your account syncs, or enter a valid server URL.');
      return;
    }
    this.config.nestjsUrl = normalizeNestjsUrl(this.config.nestjsUrl);
    this.savingBackend.set(true);
    this.backendSaveError.set(null);
    try {
      await this.nls().config.set({ nestjsUrl: this.config.nestjsUrl });
      await this.api.whenReady();
      this.nextStep();
    } catch (err: any) {
      this.backendSaveError.set(err?.error?.message || err?.message || 'Could not save server');
    } finally {
      this.savingBackend.set(false);
    }
  }

  canContinueSignIn(): boolean {
    if (this.auth.isAuthenticated()) return true;
    if (!this.authEmail.trim() || !this.authPassword) return false;
    if (this.authAccountMode() === 'signup') {
      return this.authPassword.length >= 8;
    }
    return true;
  }

  authSubmitLabel(): string {
    if (this.auth.isAuthenticated()) return 'Continue';
    if (this.signingIn() || this.savingBackend()) {
      return this.authAccountMode() === 'signup' ? 'Creating account…' : 'Signing in…';
    }
    return this.authAccountMode() === 'signup' ? 'Create account' : 'Sign in';
  }

  async saveSignInAndContinue(): Promise<void> {
    if (!this.venvReady()) {
      this.authError.set('Finish installing Babo on this computer before signing in.');
      return;
    }
    const url = normalizeNestjsUrl(this.config.nestjsUrl);
    if (!url) {
      this.authError.set('Choose an account server first (previous step).');
      return;
    }
    this.savingBackend.set(true);
    this.authError.set(null);
    try {
      await this.nls().config.set({ nestjsUrl: url });
      await this.api.whenReady();
      let didAuth = false;
      if (!this.auth.isAuthenticated()) {
        if (!this.authEmail.trim() || !this.authPassword) {
          this.authError.set('Enter your email and password, or create an account.');
          return;
        }
        this.signingIn.set(true);
        const email = this.authEmail.trim();
        const tokens = await firstValueFrom(
          this.authAccountMode() === 'signup'
            ? this.auth.register(
                email,
                this.authPassword,
                this.authDisplayName.trim() || undefined,
              )
            : this.auth.login(email, this.authPassword),
        );
        this.auth.applyTokens(tokens, false);
        this.signingIn.set(false);
        didAuth = true;
      }
      await this.loadPlatformCaps();
      await this.ensureBaboCloudAccess();
      await this.billing.refresh();
      if (didAuth) {
        this.analytics.track('setup_auth_success', {
          mode: this.authAccountMode(),
        });
      }
      if (this.requiresSetupSubscription()) {
        this.persistWizardDraft();
        this.goToStep(7);
      } else {
        this.clearWizardDraft();
        this.goToStep(8);
      }
    } catch (err: any) {
      this.authError.set(err?.error?.message || err?.message || 'Could not sign in');
      this.analytics.track('setup_auth_failed', {
        mode: this.authAccountMode(),
        error: (err?.error?.message || err?.message || 'Could not sign in').slice(0, 120),
      });
    } finally {
      this.savingBackend.set(false);
      this.signingIn.set(false);
    }
  }

  experienceRows(): ExpRow[] {
    const p = this.profile();
    if (!p) return [];
    const brain =
      p.inference.tier === 'off'
        ? 'Not configured'
        : p.inference.tier === 'hosted_babo'
          ? 'Babo Cloud'
          : p.inference.model || tierLabel(p.inference.tier);
    const voice =
      p.transcribe.tier === 'off' ? 'Off' : tierLabel(p.transcribe.tier);
    const ambient = this.ambientVisionOn()
      ? tierLabel(this.visualTier())
      : 'Off';
    const code = this.codeSearchOn() ? 'On' : 'Off';
    const backend = this.backendSummaryLabel();
    return [
      { label: 'Thinking', value: brain, ok: p.inference.tier !== 'off' },
      { label: 'Voice', value: voice, ok: p.transcribe.tier !== 'off' },
      { label: 'Screen awareness', value: ambient, ok: this.ambientVisionOn() },
      { label: 'Code search', value: code, ok: this.codeSearchOn() },
      { label: 'Account & sync', value: backend, ok: !!normalizeNestjsUrl(this.config.nestjsUrl) },
    ];
  }

  private profileForSave(): CapabilityProfile | null {
    const p = this.profile();
    if (!p) return null;
    let next = { ...p };
    if (p.inference.url) {
      next.inference = {
        ...p.inference,
        url: stripInferenceV1Suffix(p.inference.url),
      };
    }
    if (usesBaboCloudRelay(next)) {
      next = applyBaboCloudPlacements(next, this.config.nestjsUrl);
    }
    return next;
  }

  async loadPlatformCaps(): Promise<void> {
    if (!this.auth.isAuthenticated()) return;
    try {
      const caps = await firstValueFrom(this.api.getPlatformCapabilities());
      this.platformCaps.set(caps);
    } catch {
      this.platformCaps.set(null);
    }
  }

  requiresSetupSubscription(): boolean {
    if (!usesHostedBaboCloud(this.profile())) return false;
    const caps = this.platformCaps();
    if (caps && !this.billing.billingEnabledFromCaps(caps)) return false;
    const sub = this.billing.subscription();
    if (!sub) return true;
    return this.billing.needsSubscription();
  }

  async startSetupCheckout(): Promise<void> {
    this.billingCheckoutLoading.set(true);
    this.billingAlert.set(null);
    try {
      this.persistWizardDraft();
      const opened = await this.billing.openCheckout({
        flow: 'setup',
        caps: this.platformCaps(),
      });
      if (!opened) {
        this.billingAlert.set({ kind: 'error', text: 'Could not start checkout' });
        return;
      }
      this.analytics.track('setup_billing_checkout_started');
      this.billingAwaitingPayment.set(true);
      this.startBillingSubscriptionPoll();
    } catch (err: any) {
      this.billingAlert.set({
        kind: 'error',
        text: err?.error?.message || err?.message || 'Could not start checkout',
      });
    } finally {
      this.billingCheckoutLoading.set(false);
    }
  }

  async continueAfterBilling(): Promise<void> {
    this.billingConfirming.set(true);
    this.billingAlert.set(null);
    try {
      const active = await this.checkBillingActivation(false);
      if (active) {
        this.billingAlert.set(null);
        this.clearWizardDraft();
        this.goToStep(8);
        return;
      }
      if (this.billingAwaitingPayment()) {
        this.startBillingSubscriptionPoll();
        this.billingAlert.set({
          kind: 'warn',
          text: 'Payment not confirmed yet — finish in your browser, or wait a few seconds.',
        });
      } else {
        this.billingAlert.set({
          kind: 'warn',
          text: 'Subscription not active yet — start checkout or wait a moment and try again.',
        });
      }
    } finally {
      this.billingConfirming.set(false);
    }
  }

  billingPrimaryLabel(): string {
    const sub = this.billing.subscription();
    if (sub && isPaidOrComp(sub)) return 'Continue';
    if (this.billingCheckoutLoading()) return 'Opening checkout…';
    if (this.billingConfirming()) return 'Confirming…';
    if (this.billingAwaitingPayment()) return 'Check subscription';
    return 'Continue with Babo Cloud';
  }

  billingPrimaryDisabled(): boolean {
    return this.billingCheckoutLoading() || this.billingConfirming();
  }

  async onBillingPrimaryAction(): Promise<void> {
    const sub = this.billing.subscription();
    if (sub && isPaidOrComp(sub)) {
      await this.continueAfterBilling();
      return;
    }
    if (this.billingAwaitingPayment() || this.billingConfirming()) {
      await this.continueAfterBilling();
      return;
    }
    await this.startSetupCheckout();
  }

  private async checkBillingActivation(autoAdvance: boolean): Promise<boolean> {
    const sub =
      (await this.billing.syncFromStripe()) ?? (await this.billing.refresh());
    if (sub && isPaidOrComp(sub)) {
      this.stopBillingSubscriptionPoll();
      this.billingAwaitingPayment.set(false);
      this.billingConfirming.set(false);
      this.billingAlert.set(null);
      if (autoAdvance && this.step() === 7) {
        this.toast.show('Subscription active — you\'re all set.', 'info');
        this.clearWizardDraft();
        this.goToStep(8);
      }
      if (!this.billingActivatedTracked) {
        this.billingActivatedTracked = true;
        this.analytics.track('setup_billing_activated');
      }
      return true;
    }
    return false;
  }

  private startBillingSubscriptionPoll(): void {
    this.stopBillingSubscriptionPoll();
    const started = Date.now();
    const maxMs = 10 * 60 * 1000;
    void this.checkBillingActivation(true);
    this.billingPollTimer = setInterval(() => {
      if (this.step() !== 7) {
        this.stopBillingSubscriptionPoll();
        return;
      }
      void this.checkBillingActivation(true).then((active) => {
        if (active) return;
        if (Date.now() - started > maxMs) {
          this.billingConfirming.set(false);
          this.billingAlert.set({
            kind: 'warn',
            text: 'Still waiting — tap Check subscription after completing payment.',
          });
          this.stopBillingSubscriptionPoll();
        }
      });
    }, 2500);
  }

  private stopBillingSubscriptionPoll(): void {
    if (this.billingPollTimer) {
      clearInterval(this.billingPollTimer);
      this.billingPollTimer = null;
    }
  }

  private async enforceBillingStepIfNeeded(): Promise<void> {
    await this.loadPlatformCaps();
    await this.billing.refresh();
    if (this.requiresSetupSubscription()) {
      this.goToStep(7);
    }
  }

  async handleBillingReturn(status: string): Promise<void> {
    if (this.auth.isAuthenticated()) {
      await this.loadPlatformCaps();
    }

    if (status === 'success') {
      this.billingAwaitingPayment.set(true);
      this.billingAlert.set(null);
      this.goToStep(7);
      this.startBillingSubscriptionPoll();
    } else {
      this.analytics.track('setup_billing_canceled');
      this.billingAwaitingPayment.set(false);
      this.stopBillingSubscriptionPoll();
      this.toast.show('Checkout canceled — you can try again when ready.', 'info');
      this.billingAlert.set(null);
      this.goToStep(7);
    }

    void this.router.navigate([], {
      relativeTo: this.route,
      queryParams: {},
      replaceUrl: true,
    });
  }

  async ensureBaboCloudAccess(): Promise<void> {
    const p = this.profile();
    if (!p || !usesBaboCloudRelay(p)) return;

    this.provisioningCloud.set(true);
    try {
      if (p.inference.tier === 'hosted_babo') {
        if (!this.config.inferenceApiKey?.startsWith('nlsk_')) {
          const created = await firstValueFrom(
            this.apiKeys.createKey('Babo Desktop', {
              rateLimitRpm: 120,
              scopes: ['inference', 'gpu'],
            }),
          );
          if (created.key) {
            this.config.inferenceApiKey = created.key;
          }
        }
      }

      const synced = applyBaboCloudPlacements(p, this.config.nestjsUrl);
      this.profile.set(synced);
      this.syncInferenceLegacy();
      await this.nls().config.set({
        nestjsUrl: this.config.nestjsUrl,
        inferenceApiKey: this.config.inferenceApiKey,
        inferenceUrl: this.config.inferenceUrl,
        inferenceModel: this.config.inferenceModel,
      });
      await this.nls().capabilities.applyProfile(synced);

      if (p.inference.tier === 'byok_cloud' && this.config.inferenceApiKey?.trim()) {
        await this.syncByokKeyToCloud();
        await this.testBrain();
      } else if (p.inference.tier === 'hosted_babo' && this.config.inferenceApiKey) {
        await this.billing.refresh();
        if (!this.requiresSetupSubscription()) {
          await this.testBrain();
        }
      }
    } finally {
      this.provisioningCloud.set(false);
    }
  }

  async saveCapabilitiesAndContinue(): Promise<void> {
    const p = this.profileForSave();
    if (!p) return;
    this.savingCapabilities.set(true);
    try {
      this.profile.set(p);
      this.syncInferenceLegacy();
      await this.nls().config.set({
        inferenceApiKey: this.config.inferenceApiKey,
        inferenceUrl: this.config.inferenceUrl,
        inferenceModel: this.config.inferenceModel,
      });
      await this.nls().capabilities.applyProfile(p);
      if (this.shouldPrefetchLocalVision(p)) {
        this.startVisionPrefetchInBackground();
      }
      this.analytics.track('setup_extras_saved', {
        vision_on: this.ambientVisionOn(),
        code_search_on: this.codeSearchOn(),
        voice_tier: this.voiceTier(),
      });
      this.persistWizardDraft();
      this.suggestBackendFromThinking();
      if (this.preferSignInAfterConfig) {
        this.preferSignInAfterConfig = false;
        if (!this.needsAccountServerStep()) {
          await this.persistBackendUrl();
        }
        this.goToStep(6);
      } else if (!this.needsAccountServerStep()) {
        await this.persistBackendUrl();
        this.goToStep(6);
      } else {
        this.nextStep();
      }
    } finally {
      this.savingCapabilities.set(false);
    }
  }

  canContinueName(): boolean {
    return this.agentName.trim().length >= 2;
  }

  continueToNameStep(): void {
    this.nextStep();
  }

  async finish(): Promise<void> {
    const name = this.agentName.trim();
    if (name.length < 2) {
      this.launchError.set('Choose a name with at least 2 characters.');
      return;
    }

    try {
      const check = await this.nls().setup.check();
      this.venvReady.set(!!check.venvReady);
      if (!check.venvReady) {
        this.launchError.set(
          'Python environment is not ready yet. Go back to the prepare step and wait for setup to complete.',
        );
        return;
      }
    } catch {
      this.launchError.set('Could not verify Python setup. Try again from the prepare step.');
      return;
    }

    this.launching.set(true);
    this.launchError.set(null);
    this.launchMessage.set('Saving configuration...');

    try {
      let p = this.profileForSave();
      if (p && usesBaboCloudRelay(p)) {
        p = applyBaboCloudPlacements(p, this.config.nestjsUrl);
      }
      await this.nls().config.set({
        ...this.config,
        capabilityProfile: p,
        setupComplete: true,
      });

      if (p) {
        await this.nls().capabilities.applyProfile(p);
      }

      await this.api.whenReady();

      if (usesHostedBaboCloud(p)) {
        await this.loadPlatformCaps();
        await this.billing.refresh();
        if (this.requiresSetupSubscription()) {
          this.launching.set(false);
          this.launchError.set(
            'Subscribe to Babo Cloud before creating your agent.',
          );
          this.goToStep(7);
          return;
        }
      }

      this.launchMessage.set('Starting agent runtime...');
      await this.nls().runtime.start();

      this.launchMessage.set('Creating your agent...');
      const agent = await firstValueFrom(
        this.api.createAgent({
          name,
          sovereignty: 'local',
          genesisVersion: 'standard-v1',
        }),
      );

      if (p?.inference.tier === 'hosted_babo') {
        const scoped = await firstValueFrom(
          this.apiKeys.createKey(name, {
            agentId: agent.cloudId ?? agent.id,
            rateLimitRpm: 120,
            scopes: ['inference', 'gpu'],
          }),
        );
        if (scoped.key) {
          this.config.inferenceApiKey = scoped.key;
          await this.nls().config.set({ inferenceApiKey: scoped.key });
          await this.agentModels.hotReloadInference({
            inference_api_key: scoped.key,
            hf_model: p.inference.model || this.config.inferenceModel,
          });
        }
      }

      this.clearWizardDraft();
      this.day1Coach.schedule();
      this.setupCompletedThisSession = true;
      this.analytics.track('setup_completed', {
        brain_tier: p?.inference.tier ?? 'unknown',
        backend_choice: this.backendChoice(),
      });
      this.launchMessage.set('Opening Babo...');
      await new Promise((r) => setTimeout(r, 400));
      const chatAgentId = agent.runtimeAgentId || agent.id;
      this.router.navigate(['/chat', chatAgentId]);
    } catch (err: any) {
      this.launching.set(false);
      this.launchError.set(err?.message || 'Failed to finish setup.');
    }
  }

  private ensureElapsedTimerForInstall(): void {
    if (this.elapsedTimer) return;
    this.startElapsedTimer();
  }

  private startElapsedTimer(): void {
    const start = Date.now();
    this.elapsedTime.set('0:00');
    this.elapsedTimer = setInterval(() => {
      const elapsed = Math.floor((Date.now() - start) / 1000);
      const mins = Math.floor(elapsed / 60);
      const secs = elapsed % 60;
      this.elapsedTime.set(`${mins}:${secs.toString().padStart(2, '0')}`);
    }, 1_000);
  }

  private stopElapsedTimer(): void {
    if (this.elapsedTimer) {
      clearInterval(this.elapsedTimer);
      this.elapsedTimer = null;
    }
  }

  private trackSetupStep(step: number): void {
    this.analytics.track('setup_step_viewed', {
      step,
      step_name: setupStepName(step),
      highest_step_reached: this.highestStepReached,
    });
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
