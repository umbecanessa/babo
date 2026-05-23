import { Component, OnInit, OnDestroy, signal, ViewChild, ElementRef, effect } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { PlatformService } from '../../core/services/platform.service';

interface SetupConfig {
  inferenceUrl: string;
  inferenceModel: string;
  inferenceApiKey: string;
  nestjsUrl: string;
  /** @deprecated migrated from legacy config */
  vllmUrl?: string;
  hfModel?: string;
  gpuWorkerUrl?: string;
  gpuWorkerSecret?: string;
  runtimeHost?: string;
}

@Component({
  selector: 'app-setup',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="setup-page">
      <!-- Ambient -->
      <div class="ambient">
        <div class="glow glow-1"></div>
        <div class="glow glow-2"></div>
      </div>

      <div class="setup-card animate-fade-in">
        <div class="setup-header">
          <img src="assets/images/babo.png" alt="Babo" class="setup-logo" />
          <h1 class="setup-title">Babo</h1>
          <p class="setup-subtitle">Desktop Setup</p>
        </div>

        <!-- Step indicator -->
        <div class="steps">
          @for (s of steps; track s.id; let i = $index) {
            <div class="step" [class.active]="step() === i" [class.done]="step() > i">
              <span class="step-num">{{ step() > i ? '✓' : i + 1 }}</span>
              <span class="step-label">{{ s.label }}</span>
            </div>
          }
        </div>

        <!-- Step 0: Python Environment -->
        @if (step() === 0) {
          <div class="step-content animate-fade-in">
            <h2>Python Environment</h2>
            <p>Babo needs Python 3.11+ to run the agent runtime locally.</p>

            @if (setupStage() === 'idle') {
              <button class="action-btn" (click)="startSetup()">Set Up Python Environment</button>
            }

            @if (setupStage() === 'checking' || setupStage() === 'creating-venv' || setupStage() === 'installing') {
              <div class="progress-area">
                <div class="progress-bar">
                  <div class="progress-fill" [style.width.%]="setupProgress()"></div>
                </div>
                <div class="progress-meta">
                  <p class="progress-text">{{ setupMessage() }}</p>
                  <span class="elapsed-time">{{ elapsedTime() }}</span>
                </div>

                <button class="details-toggle" (click)="showDetails.set(!showDetails())">
                  <span class="chevron" [class.open]="showDetails()">&#9662;</span>
                  {{ showDetails() ? 'Hide Details' : 'Show Details' }}
                </button>

                @if (showDetails()) {
                  <div class="log-area animate-fade-in" #logArea>
                    @for (line of logLines(); track $index) {
                      <div class="log-line" [class.stderr]="line.level === 'stderr'">{{ line.message }}</div>
                    }
                  </div>
                }
              </div>
            }

            @if (setupStage() === 'ready') {
              <div class="success-badge animate-fade-in">Python environment ready</div>
              <button class="action-btn" (click)="nextStep()">Continue</button>
            }

            @if (setupStage() === 'error') {
              <div class="error-badge animate-fade-in">{{ setupError() }}</div>
              <button class="action-btn secondary" (click)="retrySetup()">Retry</button>
            }
          </div>
        }

        <!-- Step 1: Inference -->
        @if (step() === 1) {
          <div class="step-content animate-fade-in">
            <h2>Inference Provider</h2>
            <p>Connect to any OpenAI-compatible API (OpenRouter, local Ollama, vLLM, etc.).</p>

            <div class="form-group">
              <label>Inference API URL</label>
              <input type="text" [(ngModel)]="config.inferenceUrl" placeholder="https://openrouter.ai/api/v1" />
            </div>

            <div class="form-group">
              <label>Model</label>
              <input type="text" [(ngModel)]="config.inferenceModel" placeholder="openai/gpt-4o-mini" />
            </div>

            <div class="form-group">
              <label>API Key (optional)</label>
              <input type="password" [(ngModel)]="config.inferenceApiKey" placeholder="sk-..." />
            </div>

            <div class="form-group">
              <label>Backend URL (NestJS)</label>
              <input type="text" [(ngModel)]="config.nestjsUrl" placeholder="http://localhost:3000" />
            </div>

            <div class="connection-test centered">
              <button class="action-btn secondary small" (click)="testInference()" [disabled]="testing()">
                {{ testing() ? 'Testing...' : 'Test Connection' }}
              </button>
              @if (testResult()) {
                <span class="test-result" [class.ok]="testResult()!.ok" [class.fail]="!testResult()!.ok">
                  {{ testResult()!.message }}
                  @if (testResult()!.ok) {
                    <span class="latency">{{ testResult()!.latency }}ms</span>
                  }
                </span>
              }
            </div>

            <div class="step-actions">
              <button class="action-btn secondary" (click)="prevStep()">Back</button>
              <button class="action-btn" (click)="saveConfigAndNext()">Continue</button>
            </div>
          </div>
        }

        <!-- Step 2: Ready -->
        @if (step() === 2) {
          <div class="step-content animate-fade-in">
            <div class="ready-orb">
              <div class="orb"></div>
              <div class="ring ring-1"></div>
              <div class="ring ring-2"></div>
            </div>
            <h2>Ready</h2>
            <p>Your Babo desktop agent is configured. The runtime will start automatically.</p>

            <div class="config-summary">
              <div class="summary-row">
                <span class="label">Inference</span>
                <span class="value">{{ config.inferenceUrl }}</span>
              </div>
              <div class="summary-row">
                <span class="label">Backend</span>
                <span class="value">{{ config.nestjsUrl }}</span>
              </div>
              <div class="summary-row">
                <span class="label">Model</span>
                <span class="value">{{ config.inferenceModel }}</span>
              </div>
            </div>

            @if (launching()) {
              <div class="progress-area animate-fade-in">
                <div class="progress-bar">
                  <div class="progress-fill indeterminate"></div>
                </div>
                <p class="progress-text">{{ launchMessage() }}</p>
              </div>
            } @else if (launchError()) {
              <div class="error-badge animate-fade-in">{{ launchError() }}</div>
              <button class="action-btn" (click)="finish()">Retry</button>
            } @else {
              <button class="action-btn launch" (click)="finish()">Launch Babo</button>
            }
          </div>
        }
      </div>
    </div>
  `,
  styles: [`
    :host {
      display: block;
      height: 100vh;
      overflow: hidden;
    }

    .setup-page {
      position: relative;
      height: 100%;
      background: #050508;
      display: flex;
      align-items: center;
      justify-content: center;
      overflow: hidden;
    }

    /* ─── Ambient ─────────────────────────────────────────── */

    .ambient {
      position: absolute;
      inset: 0;
      pointer-events: none;
    }

    .glow {
      position: absolute;
      border-radius: 50%;
      filter: blur(150px);
      opacity: 0.12;
    }

    .glow-1 {
      width: 700px;
      height: 700px;
      background: #38bdf8;
      top: 50%;
      left: 50%;
      transform: translate(-50%, -50%);
      animation: glow-breathe 6s ease-in-out infinite;
    }

    .glow-2 {
      width: 400px;
      height: 400px;
      background: #a78bfa;
      top: 50%;
      left: 50%;
      transform: translate(-50%, -50%);
      opacity: 0.06;
      animation: glow-breathe 8s ease-in-out infinite 2s;
    }

    @keyframes glow-breathe {
      0%, 100% { opacity: 0.08; transform: translate(-50%, -50%) scale(1); }
      50% { opacity: 0.15; transform: translate(-50%, -50%) scale(1.1); }
    }

    /* ─── Card ────────────────────────────────────────────── */

    .setup-card {
      position: relative;
      z-index: 1;
      background: rgba(255, 255, 255, 0.03);
      border: 1px solid rgba(255, 255, 255, 0.06);
      border-radius: 20px;
      padding: 48px 40px;
      max-width: 520px;
      width: 100%;
      backdrop-filter: blur(20px);
    }

    /* ─── Header ──────────────────────────────────────────── */

    .setup-header {
      text-align: center;
      margin-bottom: 32px;
    }

    .setup-logo {
      display: block;
      margin: 0 auto 12px;
      width: 72px;
      height: auto;
    }

    .setup-title {
      font-size: 28px;
      font-weight: 200;
      color: #e0e0f0;
      letter-spacing: 0.15em;
      margin: 0;
    }

    .setup-subtitle {
      color: #8a8a9a;
      margin-top: 8px;
      font-size: 13px;
      letter-spacing: 0.04em;
    }

    /* ─── Steps ───────────────────────────────────────────── */

    .steps {
      display: flex;
      gap: 20px;
      justify-content: center;
      margin-bottom: 36px;
    }

    .step {
      display: flex;
      align-items: center;
      gap: 8px;
      color: #5a5a6a;
      font-size: 12px;
      letter-spacing: 0.04em;

      &.active {
        color: #e0e0f0;
        .step-num {
          background: rgba(56, 189, 248, 0.15);
          border-color: rgba(56, 189, 248, 0.4);
          color: #38bdf8;
        }
      }

      &.done {
        color: #34d399;
        .step-num {
          background: rgba(52, 211, 153, 0.1);
          border-color: rgba(52, 211, 153, 0.3);
          color: #34d399;
        }
      }
    }

    .step-num {
      width: 26px;
      height: 26px;
      border-radius: 50%;
      background: rgba(255, 255, 255, 0.03);
      border: 1px solid rgba(255, 255, 255, 0.08);
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 11px;
      font-weight: 500;
      transition: all 0.3s ease;
    }

    /* ─── Step Content ────────────────────────────────────── */

    .step-content {
      text-align: center;

      h2 {
        font-size: 20px;
        font-weight: 300;
        color: #e0e0f0;
        letter-spacing: 0.06em;
        margin: 0 0 8px;
      }

      > p {
        color: #8a8a9a;
        margin-bottom: 28px;
        font-size: 13px;
        line-height: 1.6;
        letter-spacing: 0.02em;
      }
    }

    /* ─── Buttons ─────────────────────────────────────────── */

    .action-btn {
      padding: 10px 32px;
      border: 1px solid rgba(56, 189, 248, 0.3);
      border-radius: 12px;
      background: rgba(56, 189, 248, 0.08);
      color: #b0d4f0;
      font-size: 14px;
      font-weight: 400;
      letter-spacing: 0.05em;
      cursor: pointer;
      transition: all 0.3s ease;

      &:hover:not(:disabled) {
        background: rgba(56, 189, 248, 0.15);
        border-color: rgba(56, 189, 248, 0.5);
        color: #e0f0ff;
        box-shadow: 0 0 20px rgba(56, 189, 248, 0.1);
      }

      &:disabled {
        opacity: 0.3;
        cursor: not-allowed;
      }

      &.secondary {
        border-color: rgba(255, 255, 255, 0.08);
        background: rgba(255, 255, 255, 0.03);
        color: #8a8a9a;

        &:hover:not(:disabled) {
          border-color: rgba(255, 255, 255, 0.15);
          background: rgba(255, 255, 255, 0.06);
          color: #b0b0c0;
          box-shadow: none;
        }
      }

      &.small {
        padding: 7px 20px;
        font-size: 12px;
      }

      &.launch {
        padding: 12px 48px;
        font-size: 15px;
      }
    }

    /* ─── Form ────────────────────────────────────────────── */

    .form-group {
      text-align: left;
      margin-bottom: 18px;

      label {
        display: block;
        color: #8a8a9a;
        font-size: 11px;
        margin-bottom: 6px;
        font-weight: 500;
        letter-spacing: 0.06em;
        text-transform: uppercase;
      }

      input {
        width: 100%;
        padding: 10px 14px;
        background: rgba(255, 255, 255, 0.03);
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 10px;
        color: #d0d0e0;
        font-size: 13px;
        letter-spacing: 0.02em;
        outline: none;
        transition: all 0.3s ease;
        box-sizing: border-box;

        &:focus {
          border-color: rgba(56, 189, 248, 0.3);
          background: rgba(56, 189, 248, 0.03);
        }

        &::placeholder { color: #4a4a5a; }
      }

      .hint {
        display: block;
        color: #5a5a6a;
        font-size: 11px;
        margin-top: 4px;
        letter-spacing: 0.02em;
      }
    }

    /* ─── Progress ────────────────────────────────────────── */

    .progress-area {
      margin: 24px 0;
    }

    .progress-bar {
      height: 4px;
      background: rgba(255, 255, 255, 0.06);
      border-radius: 2px;
      overflow: hidden;
    }

    .progress-fill {
      height: 100%;
      background: linear-gradient(90deg, #38bdf8, #a78bfa);
      border-radius: 2px;
      transition: width 0.3s ease;

      &.indeterminate {
        width: 30% !important;
        animation: indeterminate 1.5s ease-in-out infinite;
      }
    }

    @keyframes indeterminate {
      0% { margin-left: 0; }
      50% { margin-left: 70%; }
      100% { margin-left: 0; }
    }

    .progress-meta {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-top: 8px;
    }

    .progress-text {
      color: #8a8a9a;
      font-size: 12px;
      letter-spacing: 0.02em;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      margin: 0;
      min-width: 0;
    }

    .elapsed-time {
      color: #5a5a6a;
      font-size: 11px;
      font-family: monospace;
      letter-spacing: 0.04em;
      flex-shrink: 0;
    }

    .details-toggle {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      background: none;
      border: none;
      color: #5a5a6a;
      font-size: 11px;
      letter-spacing: 0.04em;
      cursor: pointer;
      padding: 4px 0;
      margin-top: 12px;
      transition: color 0.2s ease;

      &:hover { color: #8a8a9a; }

      .chevron {
        font-size: 9px;
        transition: transform 0.2s ease;
        &.open { transform: rotate(180deg); }
      }
    }

    .log-area {
      margin-top: 10px;
      max-height: 180px;
      overflow-y: auto;
      background: rgba(0, 0, 0, 0.4);
      border: 1px solid rgba(255, 255, 255, 0.04);
      border-radius: 8px;
      padding: 10px 12px;
      text-align: left;

      &::-webkit-scrollbar { width: 4px; }
      &::-webkit-scrollbar-track { background: transparent; }
      &::-webkit-scrollbar-thumb {
        background: rgba(255, 255, 255, 0.08);
        border-radius: 2px;
      }
    }

    .log-line {
      font-family: 'SF Mono', 'Cascadia Code', 'Fira Code', monospace;
      font-size: 10px;
      line-height: 1.6;
      color: #6a6a7a;
      word-break: break-all;

      &.stderr { color: #f87171; opacity: 0.8; }
    }

    /* ─── Badges ──────────────────────────────────────────── */

    .success-badge {
      color: #34d399;
      background: rgba(52, 211, 153, 0.08);
      border: 1px solid rgba(52, 211, 153, 0.15);
      padding: 10px 20px;
      border-radius: 12px;
      margin-bottom: 20px;
      font-size: 13px;
      letter-spacing: 0.04em;
    }

    .error-badge {
      color: #f87171;
      background: rgba(248, 113, 113, 0.08);
      border: 1px solid rgba(248, 113, 113, 0.15);
      padding: 10px 20px;
      border-radius: 12px;
      margin-bottom: 20px;
      font-size: 12px;
      letter-spacing: 0.02em;
    }

    /* ─── Connection Test ─────────────────────────────────── */

    .connection-test {
      display: flex;
      align-items: center;
      gap: 14px;
      margin-bottom: 24px;

      &.centered {
        justify-content: center;
      }
    }

    .test-result {
      font-size: 12px;
      letter-spacing: 0.02em;

      &.ok { color: #34d399; }
      &.fail { color: #f87171; }

      .latency {
        color: #6a6a7a;
        margin-left: 4px;
      }
    }

    /* ─── Advanced Toggle ─────────────────────────────────── */

    .advanced-toggle {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      background: none;
      border: none;
      color: #5a5a6a;
      font-size: 12px;
      letter-spacing: 0.04em;
      cursor: pointer;
      padding: 4px 0;
      margin-bottom: 16px;
      transition: color 0.2s ease;

      &:hover { color: #8a8a9a; }

      .chevron {
        font-size: 9px;
        transition: transform 0.2s ease;
        &.open { transform: rotate(180deg); }
      }
    }

    .advanced-fields {
      border-top: 1px solid rgba(255, 255, 255, 0.04);
      padding-top: 16px;
      margin-bottom: 8px;
    }

    /* ─── Step Actions ────────────────────────────────────── */

    .step-actions {
      display: flex;
      justify-content: space-between;
      margin-top: 24px;
    }

    /* ─── Ready Orb ───────────────────────────────────────── */

    .ready-orb {
      position: relative;
      width: 80px;
      height: 80px;
      margin: 0 auto 20px;
      display: flex;
      align-items: center;
      justify-content: center;
    }

    .orb {
      width: 16px;
      height: 16px;
      border-radius: 50%;
      background: linear-gradient(135deg, #34d399, #38bdf8);
      box-shadow:
        0 0 30px rgba(52, 211, 153, 0.5),
        0 0 80px rgba(52, 211, 153, 0.2);
      animation: orb-pulse 3s ease-in-out infinite;
      z-index: 2;
    }

    .ring {
      position: absolute;
      top: 50%;
      left: 50%;
      border-radius: 50%;
      border: 1px solid rgba(52, 211, 153, 0.12);
      transform: translate(-50%, -50%);
      animation: ring-expand 3s ease-in-out infinite;
    }

    .ring-1 { width: 40px; height: 40px; }
    .ring-2 { width: 70px; height: 70px; animation-delay: 0.5s; }

    @keyframes orb-pulse {
      0%, 100% { transform: scale(1); }
      50% { transform: scale(1.15); }
    }

    @keyframes ring-expand {
      0%, 100% { transform: translate(-50%, -50%) scale(1); opacity: 0.3; }
      50% { transform: translate(-50%, -50%) scale(1.1); opacity: 0.6; }
    }

    /* ─── Config Summary ──────────────────────────────────── */

    .config-summary {
      background: rgba(255, 255, 255, 0.03);
      border: 1px solid rgba(255, 255, 255, 0.06);
      border-radius: 12px;
      padding: 16px 20px;
      margin: 24px 0;
      text-align: left;

      .summary-row {
        display: flex;
        justify-content: space-between;
        padding: 6px 0;
        border-bottom: 1px solid rgba(255, 255, 255, 0.03);

        &:last-child { border-bottom: none; }

        .label {
          color: #6a6a7a;
          font-size: 11px;
          letter-spacing: 0.06em;
          text-transform: uppercase;
        }
        .value {
          color: #b0b0c0;
          font-size: 12px;
          font-family: monospace;
          letter-spacing: 0.02em;
        }
      }
    }

    /* ─── Animation ───────────────────────────────────────── */

    .animate-fade-in {
      animation: fade-in 0.4s ease-out;
    }

    @keyframes fade-in {
      from { opacity: 0; transform: translateY(8px); }
      to { opacity: 1; transform: translateY(0); }
    }
  `],
})
export class SetupComponent implements OnInit, OnDestroy {
  steps = [
    { id: 'python', label: 'Python' },
    { id: 'connection', label: 'Connection' },
    { id: 'ready', label: 'Ready' },
  ];

  step = signal(0);
  setupStage = signal<string>('idle');
  setupProgress = signal(0);
  setupMessage = signal('');
  setupError = signal<string | null>(null);
  elapsedTime = signal('0:00');
  showDetails = signal(false);
  logLines = signal<{ level: string; message: string }[]>([]);
  testing = signal(false);
  testResult = signal<{ ok: boolean; message: string; latency: number } | null>(null);
  showAdvanced = signal(false);
  launching = signal(false);
  launchMessage = signal('Starting agent runtime...');
  launchError = signal<string | null>(null);

  @ViewChild('logArea') private logArea?: ElementRef<HTMLDivElement>;

  config: SetupConfig = {
    inferenceUrl: 'https://openrouter.ai/api/v1',
    inferenceModel: 'openai/gpt-4o-mini',
    inferenceApiKey: '',
    nestjsUrl: 'http://localhost:3000',
  };

  private progressListener: any = null;
  private logListener: any = null;
  private elapsedTimer: any = null;

  constructor(
    private router: Router,
    private platform: PlatformService,
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
        inferenceUrl: cfg.inferenceUrl || cfg.vllmUrl || this.config.inferenceUrl,
        inferenceModel: cfg.inferenceModel || cfg.hfModel || this.config.inferenceModel,
        inferenceApiKey: cfg.inferenceApiKey || '',
      };
    } catch {}

    try {
      const check = await nls.setup.check();
      if (check.venvReady) {
        this.setupStage.set('ready');
      }
    } catch {}

    this.progressListener = (data: any) => {
      this.setupStage.set(data.stage);
      this.setupProgress.set(data.progress);
      this.setupMessage.set(data.message);
      if (data.error) this.setupError.set(data.error);
    };
    nls.on('setup:progress', this.progressListener);

    this.logListener = (data: any) => {
      const msg = (data.message || '').trim();
      if (!msg) return;
      this.logLines.update(lines => {
        const updated = [...lines, { level: data.level, message: msg }];
        return updated.length > 200 ? updated.slice(-200) : updated;
      });
    };
    nls.on('setup:log', this.logListener);
  }

  ngOnDestroy(): void {
    this.stopElapsedTimer();
    const nls = (window as any).nls;
    if (this.progressListener) {
      nls?.removeListener?.('setup:progress', this.progressListener);
    }
    if (this.logListener) {
      nls?.removeListener?.('setup:log', this.logListener);
    }
  }

  async startSetup(): Promise<void> {
    this.setupStage.set('checking');
    this.setupError.set(null);
    this.logLines.set([]);
    this.startElapsedTimer();
    try {
      const nls = (window as any).nls;
      await nls.setup.start();
      this.setupStage.set('ready');
    } catch (err: any) {
      this.setupStage.set('error');
      this.setupError.set(err?.message || 'Setup failed');
    } finally {
      this.stopElapsedTimer();
    }
  }

  retrySetup(): void {
    this.setupStage.set('idle');
    this.setupError.set(null);
  }

  nextStep(): void {
    this.step.update(s => Math.min(s + 1, this.steps.length - 1));
  }

  prevStep(): void {
    this.step.update(s => Math.max(s - 1, 0));
  }

  onHostChange(host: string): void {
    this.config.inferenceUrl = `http://${host}:8000`;
    this.config.gpuWorkerUrl = `http://${host}:8443`;
    this.testResult.set(null);
  }

  async testInference(): Promise<void> {
    this.testing.set(true);
    this.testResult.set(null);
    try {
      const nls = (window as any).nls;
      const result = await nls.config.testConnection(this.config.inferenceUrl);
      this.testResult.set(result);
    } catch (err: any) {
      this.testResult.set({ ok: false, message: err?.message || 'Test failed', latency: 0 });
    }
    this.testing.set(false);
  }

  async saveConfigAndNext(): Promise<void> {
    try {
      const nls = (window as any).nls;
      await nls.config.set(this.config);
    } catch {}
    this.nextStep();
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

  async finish(): Promise<void> {
    this.launching.set(true);
    this.launchError.set(null);
    this.launchMessage.set('Saving configuration...');

    try {
      const nls = (window as any).nls;
      await nls.config.set({ ...this.config, setupComplete: true });

      this.launchMessage.set('Starting agent runtime...');
      await nls.runtime.start();

      this.launchMessage.set('Runtime is healthy — launching dashboard...');
      await new Promise(r => setTimeout(r, 600));
      this.router.navigate(['/dashboard']);
    } catch (err: any) {
      this.launching.set(false);
      this.launchError.set(
        err?.message || 'Failed to start agent runtime. Check Python environment and inference connection.',
      );
    }
  }
}
