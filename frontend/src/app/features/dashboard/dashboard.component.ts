import { Component, OnInit, OnDestroy, signal, ViewChild } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { firstValueFrom } from 'rxjs';
import { ApiService } from '../../core/services/api.service';
import { PlatformService } from '../../core/services/platform.service';
import { WebSocketService } from '../../core/services/websocket.service';
import { Agent } from '../../core/models/agent.model';
import { AgentCardComponent } from './agent-card/agent-card.component';
import { SquadsPanelComponent } from './squads-panel/squads-panel.component';
import {
  AgentCharterModalComponent,
  CharterTab,
} from './agent-charter-modal/agent-charter-modal.component';
import { ConfirmDialogComponent } from '../../shared/confirm-dialog/confirm-dialog.component';
import { TranslateModule, TranslateService } from '@ngx-translate/core';

@Component({
  selector: 'app-dashboard',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    RouterLink,
    AgentCardComponent,
    SquadsPanelComponent,
    AgentCharterModalComponent,
    ConfirmDialogComponent,
    TranslateModule,
  ],
  templateUrl: './dashboard.component.html',
  styleUrl: './dashboard.component.scss',
})
export class DashboardComponent implements OnInit, OnDestroy {
  agents = signal<Agent[]>([]);
  loading = signal(true);
  error = signal('');
  deletingAgentId = signal<string | null>(null);
  pausingAgentId = signal<string | null>(null);
  runtimeStarting = signal(false);
  runtimeStatus = signal('');
  runtimeAttempts = signal(0);
  relayStatus = signal<Record<string, boolean>>({});
  charterAgentId = signal<string | null>(null);
  charterTab = signal<CharterTab>('job');
  charterVisible = signal(false);
  @ViewChild(SquadsPanelComponent) squadsPanel?: SquadsPanelComponent;
  private destroyed = false;
  private pollTimer: ReturnType<typeof setInterval> | null = null;

  constructor(
    private api: ApiService,
    public platform: PlatformService,
    private router: Router,
    private ws: WebSocketService,
    private translate: TranslateService,
  ) {
    this.runtimeStatus.set(this.t('dashboard.connecting'));
  }

  private t(key: string, params?: Record<string, unknown>): string {
    return this.translate.instant(key, params);
  }

  ngOnInit() {
    this.initDashboard();
  }

  ngOnDestroy() {
    this.destroyed = true;
    this.stopPolling();
  }

  private async initDashboard(): Promise<void> {
    const cachedAgents = this.api.getCachedAgents();
    const hasCachedAgents = cachedAgents.length > 0;
    if (hasCachedAgents) {
      this.agents.set(cachedAgents);
      this.loading.set(false);
    }

    if (this.platform.isElectron) {
      await this.api.whenReady();
      if (await this.api.isRuntimeReady()) {
        this.loadAgents(hasCachedAgents);
        return;
      }
      this.runtimeStarting.set(true);
      const ready = await this.waitForRuntime();
      this.runtimeStarting.set(false);
      if (!ready) {
        this.loading.set(false);
        this.error.set(this.t('dashboard.runtimeFailed'));
        return;
      }
      this.api.markRuntimeReady();
    }
    this.loadAgents(hasCachedAgents);
  }

  private async waitForRuntime(maxWait = 180_000, interval = 2_000): Promise<boolean> {
    const start = Date.now();
    const stages = [
      { at: 0, key: 'dashboard.runtime.connecting' },
      { at: 4_000, key: 'dashboard.runtime.initPython' },
      { at: 10_000, key: 'dashboard.runtime.loadModules' },
      { at: 20_000, key: 'dashboard.runtime.connectInference' },
      { at: 35_000, key: 'dashboard.runtime.loadAdapters' },
      { at: 55_000, key: 'dashboard.runtime.startScheduler' },
      { at: 75_000, key: 'dashboard.runtime.warmup' },
      { at: 100_000, key: 'dashboard.runtime.almost' },
      { at: 140_000, key: 'dashboard.runtime.stillLoading' },
    ];
    let stageIdx = 0;
    let attempt = 0;

    while (Date.now() - start < maxWait && !this.destroyed) {
      const elapsed = Date.now() - start;
      while (stageIdx < stages.length - 1 && elapsed >= stages[stageIdx + 1].at) {
        stageIdx++;
      }
      this.runtimeStatus.set(this.t(stages[stageIdx].key));
      this.runtimeAttempts.set(++attempt);

      try {
        await firstValueFrom(this.api.getHealth());
        this.api.markRuntimeReady();
        this.runtimeStatus.set(this.t('dashboard.runtime.ready'));
        return true;
      } catch {
        await new Promise(r => setTimeout(r, interval));
      }
    }
    return false;
  }

  loadAgents(silent = false) {
    if (!silent) {
      this.loading.set(true);
      this.error.set('');
    }
    this.api.getAgents().subscribe({
      next: (agents) => {
        this.agents.set(agents);
        this.loading.set(false);
        this.startPolling();
        if (this.platform.isRemote) this.loadRelayStatus();
      },
      error: (err) => {
        this.loading.set(false);
        if (!silent) {
          const msg = err?.error?.message || err?.message || this.translate.instant('dashboard.loadError');
          this.error.set(msg);
        }
      },
    });
  }

  private startPolling(): void {
    if (this.pollTimer || this.destroyed) return;
    this.pollTimer = setInterval(() => {
      if (!this.destroyed && !this.loading() && !this.runtimeStarting()) {
        this.loadAgents(true);
      }
    }, 10_000);
  }

  private stopPolling(): void {
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
  }

  private loadRelayStatus(): void {
    this.api.getAllRelayStatus().subscribe({
      next: (statuses) => {
        const map: Record<string, boolean> = {};
        for (const s of statuses) map[s.id] = s.online;
        this.relayStatus.set(map);
      },
    });
  }

  isAgentOnline(agentId: string): boolean {
    if (this.platform.isElectron) return true;
    return this.relayStatus()[agentId] ?? false;
  }

  hasOnlineAgent(): boolean {
    return Object.values(this.relayStatus()).some(v => v);
  }

  deleteAgent(id: string) {
    if (this.deletingAgentId()) return;
    this.deletingAgentId.set(id);
    this.api.deleteAgent(id).subscribe({
      next: () => {
        this.ws.leaveAgent(id);
        this.agents.update(a => a.filter(agent => agent.id !== id));
        this.deletingAgentId.set(null);
        this.loadAgents(true);
      },
      error: () => {
        this.deletingAgentId.set(null);
      },
    });
  }

  togglePause(agent: Agent) {
    if (this.pausingAgentId()) return;
    this.pausingAgentId.set(agent.id);
    const action$ = agent.userPaused
      ? this.api.unpauseAgent(agent.id)
      : this.api.pauseAgent(agent.id);
    action$.subscribe({
      next: () => {
        this.agents.update(list =>
          list.map(a =>
            a.id === agent.id ? { ...a, userPaused: !agent.userPaused } : a,
          ),
        );
        this.pausingAgentId.set(null);
      },
      error: () => {
        this.pausingAgentId.set(null);
      },
    });
  }

  get activeCount(): number {
    return this.agents().filter(a => {
      if (a.userPaused) return false;
      const rt = a.runtime;
      if (rt?.activity?.busy || rt?.activity?.user_busy) return true;
      if (rt?.consciousness?.inner_loop?.active_dreaming) return true;
      return a.runtime?.status === 'alive' || a.status === 'alive';
    }).length;
  }

  get pausedCount(): number {
    return this.agents().filter(a => a.userPaused).length;
  }

  get sleepingCount(): number {
    return this.agents().filter(a => (a.runtime?.status === 'sleeping') && !a.userPaused).length;
  }

  /** Agents not assigned to any squad — shown as standalone cards. */
  unassignedAgents(): Agent[] {
    return this.agents().filter(a => !a.squadId && !a.runtime?.squad_id);
  }

  navigateToCreate() {
    this.router.navigate(['/create']);
  }

  openCharterFromCard(agentId: string, tab: CharterTab): void {
    this.charterAgentId.set(agentId);
    this.charterTab.set(tab);
    this.charterVisible.set(true);
  }

  onCharterDismiss(saved: boolean): void {
    this.charterVisible.set(false);
    this.charterAgentId.set(null);
    if (saved) {
      this.loadAgents(true);
    }
  }

  agentLabelForCharter(id: string): string {
    const a = this.agents().find(x => x.runtimeAgentId === id || x.id === id);
    return a?.name || id.slice(0, 8);
  }
}
