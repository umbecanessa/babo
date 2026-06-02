import { Component, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { AdminApiService } from '../../core/admin-api.service';
import { ApiErrorBannerComponent } from '../../shared/api-error-banner.component';
import { formatNumber, hormoneEntries, runtimeStatusLabel, statusClass } from '../../shared/format.util';

@Component({
  selector: 'app-agent-detail',
  standalone: true,
  imports: [CommonModule, RouterLink, ApiErrorBannerComponent],
  template: `
    <a routerLink="/agents" class="back">← Agents</a>

    <app-api-error-banner [message]="error()" [forbidden]="forbidden()" />

    @if (loading()) {
      <p class="muted">Loading agent operations…</p>
    } @else if (data()) {
      <header class="page-header row-head">
        <div>
          <h1 class="page-title">{{ data()!.agent.name || 'Unnamed agent' }}</h1>
          <p class="page-desc">
            Owner: <a [routerLink]="['/users', data()!.agent.user?.id]">{{ data()!.agent.user?.email }}</a>
            · Runtime <code>{{ data()!.agent.runtimeAgentId }}</code>
          </p>
        </div>
        <span class="status-pill" [class]="statusClass(liveStatus())">{{ liveStatus() }}</span>
      </header>

      <div class="actions glass-panel">
        <button type="button" class="btn btn-ghost" (click)="reload()">Refresh</button>
        <button type="button" class="btn btn-ghost" (click)="sleep()" [disabled]="acting()">Force sleep</button>
        <button type="button" class="btn btn-ghost" (click)="evict()" [disabled]="acting()">Evict from VRAM</button>
        <button type="button" class="btn btn-ghost danger" (click)="remove()" [disabled]="acting()">Delete agent</button>
      </div>

      <div class="stat-grid">
        <div class="glass-card">
          <div class="stat-value">{{ formatNumber(data()!.live?.turn_count) }}</div>
          <div class="stat-label">Turns</div>
        </div>
        <div class="glass-card">
          <div class="stat-value">{{ formatNumber(data()!.live?.sleep_count) }}</div>
          <div class="stat-label">Sleep cycles</div>
        </div>
        <div class="glass-card">
          <div class="stat-value">{{ formatNumber(data()!.live?.facts_in_memory) }}</div>
          <div class="stat-label">Facts in memory</div>
        </div>
        <div class="glass-card">
          <div class="stat-value">{{ data()!.facts?.total ?? '—' }}</div>
          <div class="stat-label">Facts in DomainDB</div>
        </div>
      </div>

      @if (hormoneRows().length) {
        <div class="glass-card section">
          <h2>Hormones (latest)</h2>
          <div class="hormone-grid">
            @for (h of hormoneRows(); track h.name) {
              <div class="hormone-item">
                <span class="h-name">{{ h.name }}</span>
                <span class="h-val">{{ h.value | number:'1.3-3' }}</span>
              </div>
            }
          </div>
        </div>
      }

      @if (data()!.live?.heartbeat || data()!.live?.ans) {
        <div class="glass-card section two-metrics">
          @if (data()!.live?.heartbeat) {
            <div>
              <h3>Heartbeat</h3>
              <ul class="kv">
                <li>Energy {{ data()!.live.heartbeat.energy }}</li>
                <li>Mood {{ data()!.live.heartbeat.mood_label }}</li>
                <li>BPM {{ data()!.live.heartbeat.bpm }}</li>
              </ul>
            </div>
          }
          @if (data()!.live?.ans) {
            <div>
              <h3>Autonomic</h3>
              <ul class="kv">
                <li>State {{ data()!.live.ans.state }}</li>
                <li>Signals {{ data()!.live.ans.total_signals }}</li>
                <li>Phase {{ data()!.live.ans.circadian_phase }}</li>
              </ul>
            </div>
          }
        </div>
      }

      @if (data()!.chain) {
        <div class="glass-card section">
          <h2>Chain / model</h2>
          <ul class="kv">
            <li>Height {{ data()!.chain.currentHeight ?? '—' }}</li>
            <li>Blocks {{ data()!.chain.blockCount ?? '—' }}</li>
            <li>Model {{ data()!.chain.baseModel || '—' }}</li>
          </ul>
        </div>
      }

      <div class="glass-card section">
        <h2>Conversation (privacy-safe summary)</h2>
        <p class="muted">Full message bodies are not shown in the operator console.</p>
        <ul class="kv">
          <li>Messages {{ data()!.conversation?.messageCount ?? 0 }}</li>
          <li>User / assistant {{ data()!.conversation?.userMessages }} / {{ data()!.conversation?.assistantMessages }}</li>
          @if (data()!.conversation?.lastPreview) {
            <li class="preview">Last ({{ data()!.conversation.lastRole }}): {{ data()!.conversation.lastPreview }}</li>
          }
        </ul>
      </div>

      @if (data()!.facts?.sample?.length) {
        <div class="glass-card section">
          <h2>Recent facts (sample)</h2>
          <table class="data">
            <thead><tr><th>Domain</th><th>Value</th></tr></thead>
            <tbody>
              @for (f of data()!.facts.sample; track f.id) {
                <tr>
                  <td><code>{{ f.domain_path }}</code></td>
                  <td>{{ truncate(f.current_value, 120) }}</td>
                </tr>
              }
            </tbody>
          </table>
        </div>
      }

      @if (data()!.recentEvents?.length) {
        <div class="glass-card section">
          <h2>Recent events</h2>
          <table class="data compact">
            <thead><tr><th>Time</th><th>Event</th></tr></thead>
            <tbody>
              @for (e of data()!.recentEvents; track $index) {
                <tr>
                  <td>{{ e.ts | date:'short' }}</td>
                  <td><code>{{ e.event }}</code></td>
                </tr>
              }
            </tbody>
          </table>
        </div>
      }

      @if (data()!.errors?.length) {
        <div class="glass-card section warn-box">
          <h2>Partial load</h2>
          <p class="muted">Some runtime introspection endpoints failed (agent may be offline on GPU plane).</p>
        </div>
      }
    }
  `,
  styles: [`
    .back { display: inline-block; margin-bottom: 1rem; }
    .row-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem; }
    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 0.5rem;
      padding: 0.75rem 1rem;
      margin-bottom: 1rem;
    }
    .danger { color: var(--accent-danger) !important; border-color: rgba(192,57,43,0.35) !important; }
    .section { margin-top: 1rem; }
    .section h2, .section h3 { margin: 0 0 0.65rem; font-size: 1rem; }
    .hormone-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
      gap: 0.5rem;
    }
    .hormone-item {
      display: flex;
      justify-content: space-between;
      padding: 0.4rem 0.55rem;
      border-radius: var(--radius-md);
      background: rgba(124, 91, 245, 0.06);
      font-size: 0.82rem;
    }
    .h-name { color: var(--text-secondary); text-transform: capitalize; }
    .h-val { font-weight: 600; }
    .kv { margin: 0; padding-left: 1.1rem; font-size: 0.88rem; color: var(--text-secondary); }
    .kv .preview { list-style: none; margin-left: -1.1rem; margin-top: 0.5rem; color: var(--text-primary); }
    .two-metrics { display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem; }
    code { font-size: 0.72rem; }
    table.compact th, table.compact td { padding: 0.4rem 0.5rem; }
    .warn-box { border-color: rgba(229, 165, 32, 0.35); }
    .status-pill {
      display: inline-block;
      padding: 0.25rem 0.65rem;
      border-radius: 999px;
      font-size: 0.8rem;
      font-weight: 600;
      text-transform: capitalize;
    }
    .status-pill.ok { background: rgba(20, 184, 166, 0.15); color: var(--accent-success); }
    .status-pill.busy { background: rgba(124, 91, 245, 0.15); color: var(--accent-primary); }
    .status-pill.sleep { background: rgba(229, 165, 32, 0.15); color: var(--accent-warn); }
    .status-pill.bad { background: rgba(192, 57, 43, 0.12); color: var(--accent-danger); }
    .status-pill.neutral { background: rgba(0,0,0,0.06); color: var(--text-secondary); }
  `],
})
export class AgentDetailComponent implements OnInit {
  loading = signal(true);
  acting = signal(false);
  error = signal<string | null>(null);
  forbidden = signal(false);
  data = signal<any>(null);
  private dbId = '';

  formatNumber = formatNumber;
  statusClass = statusClass;

  constructor(
    private api: AdminApiService,
    private route: ActivatedRoute,
    private router: Router,
  ) {}

  hormoneRows() {
    const live = this.data()?.live?.hormones;
    if (live && typeof live === 'object' && !live.hormones) {
      return hormoneEntries(live);
    }
    return hormoneEntries(this.data()?.hormones);
  }

  liveStatus(): string {
    return runtimeStatusLabel({ live: this.data()?.live });
  }

  truncate(s: string, n: number): string {
    if (!s) return '—';
    return s.length > n ? `${s.slice(0, n)}…` : s;
  }

  async ngOnInit(): Promise<void> {
    this.dbId = this.route.snapshot.paramMap.get('id') || '';
    await this.reload();
  }

  async reload(): Promise<void> {
    this.loading.set(true);
    this.error.set(null);
    try {
      this.data.set(await this.api.agentInspect(this.dbId));
    } catch (e: unknown) {
      const err = e as { status?: number };
      this.forbidden.set(err?.status === 403);
      this.error.set(AdminApiService.errorMessage(e));
    } finally {
      this.loading.set(false);
    }
  }

  async sleep(): Promise<void> {
    const rid = this.data()?.agent?.runtimeAgentId;
    if (!rid || !confirm('Force sleep for this agent?')) return;
    this.acting.set(true);
    try {
      await this.api.sleepAgent(rid);
      await this.reload();
    } catch (e: unknown) {
      alert(AdminApiService.errorMessage(e));
    } finally {
      this.acting.set(false);
    }
  }

  async evict(): Promise<void> {
    const rid = this.data()?.agent?.runtimeAgentId;
    if (!rid || !confirm('Evict agent from VRAM?')) return;
    this.acting.set(true);
    try {
      await this.api.evictAgent(rid);
      await this.reload();
    } catch (e: unknown) {
      alert(AdminApiService.errorMessage(e));
    } finally {
      this.acting.set(false);
    }
  }

  async remove(): Promise<void> {
    if (!confirm('Permanently delete this agent from DB and runtime?')) return;
    this.acting.set(true);
    try {
      await this.api.deleteAgentDb(this.dbId);
      await this.router.navigate(['/agents']);
    } catch (e: unknown) {
      alert(AdminApiService.errorMessage(e));
    } finally {
      this.acting.set(false);
    }
  }
}
