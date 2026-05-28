import { Component, OnInit, OnDestroy, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router, RouterLink } from '@angular/router';
import { firstValueFrom } from 'rxjs';
import { ApiService } from '../../core/services/api.service';
import { PlatformService } from '../../core/services/platform.service';
import { WebSocketService } from '../../core/services/websocket.service';
import { Agent } from '../../core/models/agent.model';
import { AgentCardComponent } from './agent-card/agent-card.component';
@Component({
  selector: 'app-dashboard',
  standalone: true,
  imports: [CommonModule, RouterLink, AgentCardComponent],
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
  runtimeStatus = signal('Connecting to runtime...');
  runtimeAttempts = signal(0);
  relayStatus = signal<Record<string, boolean>>({});
  private destroyed = false;
  private pollTimer: ReturnType<typeof setInterval> | null = null;

  constructor(
    private api: ApiService,
    public platform: PlatformService,
    private router: Router,
    private ws: WebSocketService,
  ) {}

  ngOnInit() {
    this.initDashboard();
  }

  ngOnDestroy() {
    this.destroyed = true;
    this.stopPolling();
  }

  private async initDashboard(): Promise<void> {
    if (this.platform.isElectron) {
      await this.api.whenReady();
      this.runtimeStarting.set(true);
      const ready = await this.waitForRuntime();
      this.runtimeStarting.set(false);
      if (!ready) {
        this.loading.set(false);
        this.error.set('Agent runtime failed to start. Check your runtime connection.');
        return;
      }
    }
    this.loadAgents();
  }

  private async waitForRuntime(maxWait = 180_000, interval = 2_000): Promise<boolean> {
    const start = Date.now();
    const stages = [
      { at: 0, msg: 'Connecting to runtime...' },
      { at: 4_000, msg: 'Initializing Python environment...' },
      { at: 10_000, msg: 'Loading NLS server modules...' },
      { at: 20_000, msg: 'Connecting to inference...' },
      { at: 35_000, msg: 'Loading agent adapters into VRAM...' },
      { at: 55_000, msg: 'Starting consciousness scheduler...' },
      { at: 75_000, msg: 'Warming up neural pathways...' },
      { at: 100_000, msg: 'Almost there...' },
      { at: 140_000, msg: 'Still loading — hang tight...' },
    ];
    let stageIdx = 0;
    let attempt = 0;

    while (Date.now() - start < maxWait && !this.destroyed) {
      const elapsed = Date.now() - start;
      while (stageIdx < stages.length - 1 && elapsed >= stages[stageIdx + 1].at) {
        stageIdx++;
      }
      this.runtimeStatus.set(stages[stageIdx].msg);
      this.runtimeAttempts.set(++attempt);

      try {
        await firstValueFrom(this.api.getHealth());
        this.runtimeStatus.set('Runtime ready — loading agents...');
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
          const msg = err?.error?.message || err?.message || 'Failed to load agents';
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

  navigateToCreate() {
    this.router.navigate(['/create']);
  }
}
