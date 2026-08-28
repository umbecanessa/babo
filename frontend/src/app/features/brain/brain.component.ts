import { Component, OnInit, OnDestroy, signal, computed, ViewChild, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute } from '@angular/router';
import { Subject, takeUntil } from 'rxjs';
import { ApiService } from '../../core/services/api.service';
import { WebSocketService } from '../../core/services/websocket.service';
import { StatusBadgeComponent } from '../../shared/components/status-badge.component';
import { LineChartComponent, LineChartSeries } from '../../shared/components/line-chart.component';
import { TimeAgoPipe } from '../../shared/pipes/time-ago.pipe';
import type {
  Agent,
  AgentRuntimeStatus,
  ChainState,
  HormoneHistory,
  NetworkHistory,
  SignalHistory,
  EventsResponse,
  WorkingMemoryStatus,
  NarrativeStatus,
  TheoryOfMindStatus,
  NetworkDynamicsStatus,
} from '../../core/models/agent.model';

import { TranslateModule, TranslateService } from '@ngx-translate/core';

@Component({
  selector: 'app-brain',
  standalone: true,
  imports: [CommonModule, FormsModule, StatusBadgeComponent, LineChartComponent, TimeAgoPipe, TranslateModule],
  styleUrl: './brain.component.scss',
  template: `
    <div class="brain-container">
      <header class="brain-header">
        <div class="header-left">
          <h1 class="agent-name">{{ agent()?.name || ('brain.agentFallback' | translate) }}</h1>
          <app-status-badge [status]="displayStatus()" [label]="displayStatus()"></app-status-badge>
        </div>
        <div class="header-actions">
          <button
            class="action-btn sleep-btn"
            [disabled]="sleeping()"
            (click)="onSleep()"
          >
            {{ sleeping() ? ('brain.sleeping' | translate) : ('brain.sleep' | translate) }}
          </button>
        </div>
      </header>

      <nav class="tabs">
        @for (t of tabs; track t.id; let i = $index) {
          @if (i > 0 && tabs[i - 1].group !== t.group) {
            <span class="tab-separator"></span>
          }
          <button
            class="tab-btn"
            [class.active]="activeTab() === t.id"
            (click)="activeTab.set(t.id)"
          >
            {{ ('brain.tabs.' + t.id) | translate }}
          </button>
        }
      </nav>

      <main class="brain-content">
        <!-- ========== Overview ========== -->
        @if (activeTab() === 'overview') {
          <div class="tab-panel">
            <div class="stats-grid">
              <div class="stat-card">
                <span class="stat-label">{{ 'brain.stats.status' | translate }}</span>
                <span class="stat-value">{{ displayStatus() }}</span>
              </div>
              <div class="stat-card">
                <span class="stat-label">{{ 'brain.stats.chainHeight' | translate }}</span>
                <span class="stat-value">{{ chain()?.current_height ?? '-' }}</span>
              </div>
              <div class="stat-card">
                <span class="stat-label">{{ 'brain.stats.facts' | translate }}</span>
                <span class="stat-value">{{ status()?.facts_in_memory ?? '-' }}</span>
              </div>
              <div class="stat-card">
                <span class="stat-label">{{ 'brain.stats.turns' | translate }}</span>
                <span class="stat-value">{{ status()?.turn_count ?? '-' }}</span>
              </div>
              <div class="stat-card">
                <span class="stat-label">{{ 'brain.stats.sleeps' | translate }}</span>
                <span class="stat-value">{{ status()?.sleep_count ?? '-' }}</span>
              </div>
              <div class="stat-card">
                <span class="stat-label">{{ 'brain.stats.genesisVersion' | translate }}</span>
                <span class="stat-value mono">{{ status()?.genesis_version ?? agent()?.genesisVersion ?? '-' }}</span>
              </div>
              <div class="stat-card">
                <span class="stat-label">{{ 'brain.stats.baseModel' | translate }}</span>
                <span class="stat-value mono">{{ chain()?.base_model ?? '-' }}</span>
              </div>
              <div class="stat-card">
                <span class="stat-label">{{ 'brain.stats.soulHash' | translate }}</span>
                <span class="stat-value mono truncated">{{ chain()?.soul_hash ?? '-' }}</span>
              </div>
              <div class="stat-card">
                <span class="stat-label">{{ 'brain.stats.sovereigntyMode' | translate }}</span>
                <span class="stat-value">{{ chain()?.sovereignty_mode ?? '-' }}</span>
              </div>
              <div class="stat-card">
                <span class="stat-label">{{ 'brain.stats.vram' | translate }}</span>
                <span class="stat-value" [class.in-vram]="status()?.in_vram">{{ status()?.in_vram ? ('brain.stats.inVram' | translate) : ('brain.stats.evicted' | translate) }}</span>
              </div>
              @if (status()?.heartbeat?.energy != null) {
                <div class="stat-card stat-card-energy">
                  <span class="stat-label">{{ 'brain.stats.energy' | translate }}</span>
                  <span class="stat-value">{{ ((status()?.heartbeat?.energy ?? 0) * 100).toFixed(0) }}%</span>
                </div>
              }
              @if (status()?.heartbeat?.mood_label) {
                <div class="stat-card">
                  <span class="stat-label">{{ 'brain.stats.mood' | translate }}</span>
                  <span class="stat-value">{{ status()?.heartbeat?.mood_label }}</span>
                </div>
              }
              @if (status()?.heartbeat?.momentum) {
                <div class="stat-card">
                  <span class="stat-label">{{ 'brain.stats.momentum' | translate }}</span>
                  <span class="stat-value">{{ status()?.heartbeat?.momentum }}</span>
                </div>
              }
              @if (status()?.narrative?.narrative_coherence != null) {
                <div class="stat-card">
                  <span class="stat-label">{{ 'brain.stats.coherence' | translate }}</span>
                  <span class="stat-value">{{ ((status()?.narrative?.narrative_coherence ?? 0) * 100).toFixed(0) }}%</span>
                </div>
              }
              @if (status()?.predictive_processing?.average_pe != null) {
                <div class="stat-card">
                  <span class="stat-label">{{ 'brain.stats.predError' | translate }}</span>
                  <span class="stat-value">{{ (status()?.predictive_processing?.average_pe ?? 0).toFixed(3) }}</span>
                </div>
              }
              @if (status()?.network_dynamics?.dominant_label) {
                <div class="stat-card">
                  <span class="stat-label">{{ 'brain.stats.network' | translate }}</span>
                  <span class="stat-value">{{ status()?.network_dynamics?.dominant_label }}</span>
                </div>
              }
            </div>

            <section class="section hormones-section">
              <h3>{{ 'brain.overview.hormones' | translate }}</h3>
              <div class="hormone-gauges">
                @for (h of hormoneList(); track h.key) {
                  <div class="hormone-gauge">
                    <span class="hormone-label">{{ h.label }}</span>
                    <div class="gauge-bar-container">
                      <div class="gauge-bar" [style.width.%]="h.value * 100" [style.background]="h.color"></div>
                      <div class="baseline" [style.left.%]="50"></div>
                    </div>
                    <span class="hormone-value">{{ (h.value * 100).toFixed(0) }}%</span>
                  </div>
                }
              </div>
            </section>

            <section class="section ans-section">
              <h3>{{ 'brain.overview.ans' | translate }}</h3>
              <div class="ans-indicator" [class]="ansState()">
                <span class="ans-dot"></span>
                <span class="ans-label">{{ ansState() }}</span>
                @if (status()?.ans) {
                  <span class="ans-meta">
                    {{ 'brain.overview.signalsMeta' | translate:{ total: status()?.ans?.total_signals, learnable: status()?.ans?.learnable_signals } }}
                  </span>
                }
              </div>
            </section>

            <section class="section neural-activity-section">
              <h3>{{ 'brain.overview.neural' | translate }} <span class="live-dot" [class.active]="probeSignals()?.midGeneration"></span></h3>
              @if (probeSignals()) {
                <div class="probe-fired-pills">
                  @for (f of probeSignals()!.fired; track f) {
                    <span class="probe-pill fired">{{ f }}</span>
                  }
                </div>
                <div class="probe-bars">
                  @for (entry of sortedProbeSignals(); track entry.key) {
                    <div class="probe-bar-row">
                      <span class="probe-bar-label">{{ entry.key }}</span>
                      <div class="probe-bar-track">
                        <div class="probe-bar-fill"
                             [style.width.%]="entry.value * 100"
                             [class.fired]="entry.value > 0.3">
                        </div>
                      </div>
                      <span class="probe-bar-value">{{ (entry.value * 100).toFixed(0) }}</span>
                    </div>
                  }
                </div>
              } @else {
                <div class="empty-state">{{ 'brain.empty.probe' | translate }}</div>
              }
            </section>

            @if (status()?.network_dynamics) {
              <section class="section network-dominance-section">
                <h3>{{ 'brain.overview.networkDominance' | translate }}</h3>
                <div class="network-overview-bars">
                  <div class="network-overview-bar">
                    <span class="net-label">ECN</span>
                    <div class="net-bar-track"><div class="net-bar-fill ecn" [style.width.%]="(status()?.network_dynamics?.ecn ?? 0) * 100"></div></div>
                    <span class="net-val">{{ ((status()?.network_dynamics?.ecn ?? 0) * 100).toFixed(0) }}%</span>
                  </div>
                  <div class="network-overview-bar">
                    <span class="net-label">SN</span>
                    <div class="net-bar-track"><div class="net-bar-fill sn" [style.width.%]="(status()?.network_dynamics?.sn ?? 0) * 100"></div></div>
                    <span class="net-val">{{ ((status()?.network_dynamics?.sn ?? 0) * 100).toFixed(0) }}%</span>
                  </div>
                  <div class="network-overview-bar">
                    <span class="net-label">DMN</span>
                    <div class="net-bar-track"><div class="net-bar-fill dmn" [style.width.%]="(status()?.network_dynamics?.dmn ?? 0) * 100"></div></div>
                    <span class="net-val">{{ ((status()?.network_dynamics?.dmn ?? 0) * 100).toFixed(0) }}%</span>
                  </div>
                </div>
                <div class="net-state-label">{{ 'brain.overview.state' | translate:{ label: status()?.network_dynamics?.dominant_label } }}</div>
              </section>
            }

            @if (networkChartSeries().length) {
              <section class="section">
                <h3>{{ 'brain.overview.networkTimeline' | translate }}</h3>
                <nls-line-chart [series]="networkChartSeries()" [height]="180" [xLabel]="('brain.overview.turn' | translate)" [yMin]="0" [yMax]="1"></nls-line-chart>
              </section>
            }
          </div>
        }

        <!-- ========== Hormones ========== -->
        @if (activeTab() === 'hormones') {
          <div class="tab-panel">
            <div class="hormone-gauges">
              @for (h of hormoneList(); track h.key) {
                <div class="hormone-gauge">
                  <span class="hormone-label">{{ h.label }}</span>
                  <div class="gauge-bar-container">
                    <div class="gauge-bar" [style.width.%]="h.value * 100" [style.background]="h.color"></div>
                    <div class="baseline" [style.left.%]="50"></div>
                  </div>
                  <span class="hormone-value">{{ (h.value * 100).toFixed(0) }}%</span>
                </div>
              }
            </div>
            @if (hormoneChartSeries().length) {
              <section class="section">
                <h3>{{ 'brain.overview.hormoneTimeline' | translate }}</h3>
                <nls-line-chart [series]="hormoneChartSeries()" [height]="220" [xLabel]="('brain.overview.turn' | translate)" yLabel="" [yMin]="0" [yMax]="1"></nls-line-chart>
              </section>
            }
          </div>
        }

        <!-- ========== Visual Cortex ========== -->
        @if (activeTab() === 'visual-cortex') {
          <div class="tab-panel vc-panel">
            <div class="vc-toolbar">
              <div class="vc-toolbar-left">
                <select class="filter-select" [ngModel]="vcChannelFilter()" (ngModelChange)="vcChannelFilter.set($event); loadVisualCortex()">
                  <option value="">{{ 'brain.vc.allChannels' | translate }}</option>
                  <option value="agent">{{ 'brain.vc.channelAgent' | translate }}</option>
                  <option value="user">{{ 'brain.vc.channelUser' | translate }}</option>
                </select>
                <button class="action-btn" (click)="loadVisualCortex()">{{ 'brain.vc.refresh' | translate }}</button>
                <button class="action-btn" [class.active]="vcAutoRefresh()" (click)="toggleVcAutoRefresh()">
                  {{ vcAutoRefresh() ? ('brain.vc.stopAuto' | translate) : ('brain.vc.autoRefresh' | translate) }}
                </button>
              </div>
              @if (vcData(); as vc) {
                <div class="vc-toolbar-right">
                  <span class="vc-status-pill" [class.running]="vc.running" [class.stopped]="!vc.running">
                    {{ vc.running ? ('brain.vc.running' | translate) : (vc.enabled ? ('brain.vc.stopped' | translate) : ('brain.vc.disabled' | translate)) }}
                  </span>
                  @if (vc.config?.agent_active) {
                    <span class="vc-status-pill active">{{ 'brain.vc.agentActive' | translate }}</span>
                  }
                  @if (vc.config?.model_preference) {
                    <span class="vc-meta">{{ 'brain.vc.model' | translate:{ model: vc.config.model_preference } }}</span>
                  }
                  @if (vc.config?.agent_fps) {
                    <span class="vc-meta">{{ 'brain.vc.fps' | translate:{ agent: (vc.config.agent_fps | number:'1.1-1'), user: (vc.config.user_fps | number:'1.1-1') } }}</span>
                  }
                  <span class="vc-meta">{{ 'brain.vc.buffer' | translate:{ total: vc.total, size: (vc.config?.buffer_size || '?') } }}</span>
                </div>
              }
            </div>

            @if (vcData(); as vc) {
              @if (!vc.enabled) {
                <div class="empty-state">{{ 'brain.vc.disabledEmpty' | translate }}</div>
              } @else if (vc.events.length === 0) {
                <div class="empty-state">{{ vcChannelFilter() ? ('brain.vc.noEventsChannel' | translate:{ channel: vcChannelFilter() }) : ('brain.vc.noEvents' | translate) }}</div>
              } @else {
                <div class="vc-event-list">
                  @for (ev of vc.events.slice().reverse(); track $index) {
                    <div class="vc-event" [class.agent]="ev.channel === 'agent'" [class.user]="ev.channel === 'user'">
                      <div class="vc-event-header">
                        <span class="vc-channel-badge" [class.agent]="ev.channel === 'agent'" [class.user]="ev.channel === 'user'">
                          {{ ev.channel }}
                        </span>
                        @if (ev.agent_tool) {
                          <span class="vc-tool-badge">{{ ev.agent_tool }}</span>
                        }
                        <span class="vc-app">{{ ev.app_name }}</span>
                        @if (ev.window_title) {
                          <span class="vc-title muted">{{ ev.window_title | slice:0:60 }}</span>
                        }
                        <span class="vc-ts muted">{{ ev.timestamp * 1000 | date:'HH:mm:ss' }}</span>
                        @if (ev.confidence < 1) {
                          <span class="vc-confidence muted">{{ (ev.confidence * 100) | number:'1.0-0' }}%</span>
                        }
                      </div>
                      @if (ev.description) {
                        <div class="vc-description">{{ ev.description }}</div>
                      }
                      @if (ev.change_summary) {
                        <div class="vc-change"><span class="vc-label">{{ 'brain.vc.changed' | translate }}</span> {{ ev.change_summary }}</div>
                      }
                      @if (ev.ocr_text) {
                        <details class="vc-ocr-details">
                          <summary class="vc-label">{{ 'brain.vc.ocr' | translate }}</summary>
                          <pre class="vc-ocr">{{ ev.ocr_text }}</pre>
                        </details>
                      }
                    </div>
                  }
                </div>
              }
            } @else {
              <div class="empty-state">{{ 'brain.vc.loading' | translate }}</div>
            }
          </div>
        }

        <!-- ========== Signals ========== -->
        @if (activeTab() === 'signals') {
          <div class="tab-panel">
            <div class="signals-header">
              <span class="ans-badge" [class]="ansState()">{{ ansState() }}</span>
            </div>
            <div class="signal-type-pills">
              @for (tc of signalTypeCounts(); track tc.type) {
                <span class="pill">{{ tc.type }}: {{ tc.count }}</span>
              }
            </div>
            <div class="signal-list">
              @for (s of recentSignals(); track $index) {
                <div class="signal-row">
                  <span class="signal-dot" [class]="s.signal_type"></span>
                  <span class="signal-type">{{ s.signal_type }}</span>
                  <span class="signal-path mono">{{ s.domain_path }}</span>
                  <span class="signal-ts muted">{{ s.ts | timeAgo }}</span>
                </div>
              }
              @if (recentSignals().length === 0) {
                <div class="empty-state">{{ 'brain.empty.signals' | translate }}</div>
              }
            </div>
          </div>
        }

        <!-- ========== Events ========== -->
        @if (activeTab() === 'events') {
          <div class="tab-panel">
            <div class="events-toolbar">
              <select class="filter-select" [(ngModel)]="eventFilter" (ngModelChange)="loadEvents()">
                <option value="">{{ 'brain.events.all' | translate }}</option>
                @for (et of eventTypes(); track et) {
                  <option [value]="et">{{ et }}</option>
                }
              </select>
            </div>
            <div class="event-list">
              @for (ev of events(); track $index) {
                <div class="event-item">
                  <button class="event-header" (click)="toggleEvent(ev.ts)">
                    <span class="event-ts muted">{{ ev.ts | timeAgo }}</span>
                    <span class="event-name">{{ ev.event }}</span>
                  </button>
                  @if (expandedEvent() === ev.ts) {
                    <div class="event-details">
                      <pre><code>{{ formatJson(ev.data) }}</code></pre>
                    </div>
                  }
                </div>
              }
              @if (events().length === 0) {
                <div class="empty-state">{{ 'brain.empty.events' | translate }}</div>
              }
            </div>
          </div>
        }

        <!-- ========== Schedule ========== -->
        @if (activeTab() === 'schedule') {
          <div class="tab-panel">
            <div class="schedule-section">
              <h3 class="section-heading">{{ 'brain.schedule.title' | translate }}</h3>
              <p class="section-desc">{{ 'brain.schedule.desc' | translate }}</p>

              <div class="schedule-grid">
                <div class="schedule-field">
                  <label class="schedule-label">{{ 'brain.schedule.bedtime' | translate }}</label>
                  <input type="time" class="schedule-input" [(ngModel)]="scheduleBedtime" />
                </div>
                <div class="schedule-field">
                  <label class="schedule-label">{{ 'brain.schedule.wakeTime' | translate }}</label>
                  <input type="time" class="schedule-input" [(ngModel)]="scheduleWakeTime" />
                </div>
                <div class="schedule-field">
                  <label class="schedule-label">{{ 'brain.schedule.timezone' | translate }}</label>
                  <select class="schedule-input" [(ngModel)]="scheduleTimezone">
                    <option value="UTC">{{ 'brain.schedule.tz.utc' | translate }}</option>
                    <option value="America/New_York">{{ 'brain.schedule.tz.eastern' | translate }}</option>
                    <option value="America/Chicago">{{ 'brain.schedule.tz.central' | translate }}</option>
                    <option value="America/Denver">{{ 'brain.schedule.tz.mountain' | translate }}</option>
                    <option value="America/Los_Angeles">{{ 'brain.schedule.tz.pacific' | translate }}</option>
                    <option value="Europe/London">{{ 'brain.schedule.tz.london' | translate }}</option>
                    <option value="Europe/Berlin">{{ 'brain.schedule.tz.centralEurope' | translate }}</option>
                    <option value="Europe/Rome">{{ 'brain.schedule.tz.rome' | translate }}</option>
                    <option value="Asia/Tokyo">{{ 'brain.schedule.tz.tokyo' | translate }}</option>
                    <option value="Asia/Shanghai">{{ 'brain.schedule.tz.shanghai' | translate }}</option>
                    <option value="Australia/Sydney">{{ 'brain.schedule.tz.sydney' | translate }}</option>
                  </select>
                </div>
                <div class="schedule-field schedule-toggle-field">
                  <label class="schedule-label">{{ 'brain.schedule.wakeOnMessage' | translate }}</label>
                  <label class="schedule-toggle">
                    <input type="checkbox" [(ngModel)]="scheduleWakeOnMessage" />
                    <span class="schedule-toggle-label">{{ scheduleWakeOnMessage ? ('brain.schedule.yes' | translate) : ('brain.schedule.no' | translate) }}</span>
                  </label>
                </div>
              </div>

              <h3 class="section-heading" style="margin-top: 1.5rem">{{ 'brain.schedule.napTitle' | translate }}</h3>
              <p class="section-desc">{{ 'brain.schedule.napDesc' | translate }}</p>

              <div class="schedule-grid">
                <div class="schedule-field">
                  <label class="schedule-label">{{ 'brain.schedule.napStart' | translate }}</label>
                  <input type="time" class="schedule-input" [(ngModel)]="scheduleNapStart" />
                </div>
                <div class="schedule-field">
                  <label class="schedule-label">{{ 'brain.schedule.napEnd' | translate }}</label>
                  <input type="time" class="schedule-input" [(ngModel)]="scheduleNapEnd" />
                </div>
              </div>

              <div class="schedule-actions">
                <button class="schedule-save-btn" (click)="saveSchedule()" [disabled]="scheduleSaving()">
                  {{ scheduleSaving() ? ('common.saving' | translate) : ('brain.schedule.save' | translate) }}
                </button>
                @if (scheduleSaved()) {
                  <span class="schedule-saved-msg">{{ 'brain.schedule.saved' | translate }}</span>
                }
              </div>
            </div>
          </div>
        }

        <!-- ========== Self-State ========== -->
        @if (activeTab() === 'self-state') {
          <div class="tab-panel">
            <section class="section">
              <h3>{{ 'brain.selfState.title' | translate }}</h3>
              @if (status()?.heartbeat; as hb) {
                <div class="self-state-grid">
                  <div class="self-state-item">
                    <span class="ss-label">{{ 'brain.selfState.energy' | translate }}</span>
                    <div class="ss-bar-track"><div class="ss-bar-fill energy" [style.width.%]="(hb.energy ?? 0) * 100"></div></div>
                    <span class="ss-val">{{ ((hb.energy ?? 0) * 100).toFixed(0) }}%</span>
                  </div>
                  <div class="self-state-item">
                    <span class="ss-label">{{ 'brain.selfState.valence' | translate }}</span>
                    <div class="ss-bar-track"><div class="ss-bar-fill valence" [style.width.%]="((hb.valence ?? 0) + 1) / 2 * 100"></div></div>
                    <span class="ss-val">{{ (hb.valence ?? 0).toFixed(2) }}</span>
                    @if (hb.delta_valence && Math.abs(hb.delta_valence) > 0.02) {
                      <span class="ss-delta" [class.positive]="hb.delta_valence > 0" [class.negative]="hb.delta_valence < 0">{{ hb.delta_valence > 0 ? '+' : '' }}{{ hb.delta_valence.toFixed(3) }}</span>
                    }
                  </div>
                  <div class="self-state-item">
                    <span class="ss-label">{{ 'brain.selfState.arousal' | translate }}</span>
                    <div class="ss-bar-track"><div class="ss-bar-fill arousal" [style.width.%]="(hb.arousal ?? 0) * 100"></div></div>
                    <span class="ss-val">{{ (hb.arousal ?? 0).toFixed(2) }}</span>
                    @if (hb.delta_arousal && Math.abs(hb.delta_arousal) > 0.02) {
                      <span class="ss-delta" [class.positive]="hb.delta_arousal > 0" [class.negative]="hb.delta_arousal < 0">{{ hb.delta_arousal > 0 ? '+' : '' }}{{ hb.delta_arousal.toFixed(3) }}</span>
                    }
                  </div>
                  <div class="self-state-item">
                    <span class="ss-label">{{ 'brain.selfState.coherence' | translate }}</span>
                    <div class="ss-bar-track"><div class="ss-bar-fill coherence" [style.width.%]="(hb.coherence ?? 0) * 100"></div></div>
                    <span class="ss-val">{{ (hb.coherence ?? 0).toFixed(2) }}</span>
                    @if (hb.delta_coherence && Math.abs(hb.delta_coherence) > 0.02) {
                      <span class="ss-delta" [class.positive]="hb.delta_coherence > 0" [class.negative]="hb.delta_coherence < 0">{{ hb.delta_coherence > 0 ? '+' : '' }}{{ hb.delta_coherence.toFixed(3) }}</span>
                    }
                  </div>
                  <div class="self-state-item">
                    <span class="ss-label">{{ 'brain.selfState.engagement' | translate }}</span>
                    <div class="ss-bar-track"><div class="ss-bar-fill engagement" [style.width.%]="(hb.engagement ?? 0) * 100"></div></div>
                    <span class="ss-val">{{ (hb.engagement ?? 0).toFixed(2) }}</span>
                  </div>
                  <div class="self-state-item">
                    <span class="ss-label">{{ 'brain.selfState.bonding' | translate }}</span>
                    <div class="ss-bar-track"><div class="ss-bar-fill bonding" [style.width.%]="(hb.bonding ?? 0) * 100"></div></div>
                    <span class="ss-val">{{ (hb.bonding ?? 0).toFixed(2) }}</span>
                  </div>
                </div>
                <div class="self-state-meta">
                  @if (hb.mood_label) { <span class="ss-chip">{{ 'brain.selfState.mood' | translate:{ value: hb.mood_label } }}</span> }
                  @if (hb.momentum) { <span class="ss-chip">{{ 'brain.selfState.momentum' | translate:{ value: hb.momentum } }}</span> }
                  @if (hb.felt_idle) { <span class="ss-chip">{{ 'brain.selfState.idle' | translate:{ value: hb.felt_idle } }}</span> }
                  @if (hb.flow) { <span class="ss-chip">{{ 'brain.selfState.flow' | translate:{ value: hb.flow } }}</span> }
                </div>
              } @else {
                <div class="empty-state">{{ 'brain.selfState.empty' | translate }}</div>
              }
            </section>
          </div>
        }

        <!-- ========== Network Dynamics ========== -->
        @if (activeTab() === 'network') {
          <div class="tab-panel">
            @if (networkData(); as nd) {
              <section class="section">
                <h3>{{ 'brain.networkDetail.activation' | translate }}</h3>
                <div class="network-detail-bars">
                  <div class="nd-bar"><span class="nd-label">{{ 'brain.networkDetail.ecn' | translate }}</span><div class="nd-track"><div class="nd-fill ecn" [style.width.%]="nd.ecn * 100"></div></div><span class="nd-val">{{ (nd.ecn * 100).toFixed(0) }}%</span></div>
                  <div class="nd-bar"><span class="nd-label">{{ 'brain.networkDetail.sn' | translate }}</span><div class="nd-track"><div class="nd-fill sn" [style.width.%]="nd.sn * 100"></div></div><span class="nd-val">{{ (nd.sn * 100).toFixed(0) }}%</span></div>
                  <div class="nd-bar"><span class="nd-label">{{ 'brain.networkDetail.dmn' | translate }}</span><div class="nd-track"><div class="nd-fill dmn" [style.width.%]="nd.dmn * 100"></div></div><span class="nd-val">{{ (nd.dmn * 100).toFixed(0) }}%</span></div>
                </div>
                <p class="nd-state">{{ 'brain.networkDetail.dominant' | translate:{ label: nd.dominant_label, count: nd.transition_count } }}</p>
              </section>
              @if (nd.recent_transitions?.length) {
                <section class="section">
                  <h3>{{ 'brain.networkDetail.recentTransitions' | translate }}</h3>
                  <div class="transition-list">
                    @for (t of nd.recent_transitions; track $index) {
                      <div class="transition-row">
                        <span class="transition-from">{{ t.from_network }}</span>
                        <span class="transition-arrow">&rarr;</span>
                        <span class="transition-to">{{ t.to_network }}</span>
                        <span class="transition-trigger muted">{{ t.trigger }}</span>
                      </div>
                    }
                  </div>
                </section>
              }
              @if (networkChartSeries().length) {
                <section class="section">
                  <h3>{{ 'brain.networkDetail.activationTimeline' | translate }}</h3>
                  <nls-line-chart [series]="networkChartSeries()" [height]="220" [xLabel]="('brain.overview.turn' | translate)" [yMin]="0" [yMax]="1"></nls-line-chart>
                </section>
              }
            } @else {
              <div class="empty-state">{{ 'brain.networkDetail.empty' | translate }}</div>
            }
          </div>
        }

        <!-- ========== Working Memory ========== -->
        @if (activeTab() === 'working-memory') {
          <div class="tab-panel">
            @if (wmData(); as wm) {
              <div class="wm-overview-stats">
                <div class="wm-ws-switcher">
                  <button class="wm-ws-btn"
                    [class.active]="wmDisplayWorkspace() === 'professional'"
                    (click)="wmViewWorkspace.set('professional')">
                    {{ 'brain.wm.professional' | translate }}
                    @if (wmActiveWorkspace() === 'professional') {
                      <span class="wm-ws-active-dot"></span>
                    }
                  </button>
                  <button class="wm-ws-btn"
                    [class.active]="wmDisplayWorkspace() === 'personal'"
                    (click)="wmViewWorkspace.set('personal')">
                    {{ 'brain.wm.personal' | translate }}
                    @if (wmActiveWorkspace() === 'personal') {
                      <span class="wm-ws-active-dot"></span>
                    }
                  </button>
                </div>
                @if (wm.slot_count || wm.goal_count || wm.intention_count) {
                  <span class="wm-stat">{{ 'brain.wm.slotsStat' | translate:{ used: wm.slot_count, max: wm.max_slots } }}</span>
                  <span class="wm-stat">{{ 'brain.wm.goalsStat' | translate:{ count: wm.goal_count } }}</span>
                  <span class="wm-stat">{{ 'brain.wm.intentionsStat' | translate:{ count: wm.intention_count } }}</span>
                }
                @if (wm.common_slot_count) {
                  <span class="wm-stat muted">{{ 'brain.wm.commonStat' | translate:{ count: wm.common_slot_count } }}</span>
                }
              </div>
              @if (wmPrimaryGoals().length) {
                <section class="section">
                  <h3>{{ 'brain.wm.goals' | translate }}</h3>
                  @for (g of wmPrimaryGoals(); track $index) {
                    <div class="wm-card goal-card">
                      <span class="wm-card-badge">{{ g.level }}</span>
                      <span class="wm-card-content">{{ g.content }}</span>
                    </div>
                  }
                </section>
              }
              @if (wmPrimarySlots().length) {
                <section class="section">
                  <h3>{{ 'brain.wm.slots' | translate }} <span class="muted">({{ ('brain.wm.' + wmDisplayWorkspace()) | translate }})</span></h3>
                  @for (s of wmPrimarySlots(); track $index) {
                    <div class="wm-card slot-card" [style.opacity]="Math.max(0.4, s.salience)">
                      <span class="wm-card-badge">{{ s.type }}</span>
                      <span class="wm-card-content">{{ s.content }}</span>
                      @if (s.domain) { <span class="wm-card-domain muted">{{ s.domain }}</span> }
                    </div>
                  }
                </section>
              }
              @if (wm.intentions?.length) {
                <section class="section">
                  <h3>{{ 'brain.wm.intentions' | translate }}</h3>
                  @for (it of wm.intentions; track $index) {
                    <div class="wm-card intention-card">
                      <span class="wm-card-badge">{{ it.trigger }}</span>
                      <span class="wm-card-content">{{ it.content }}</span>
                    </div>
                  }
                </section>
              }
              <!-- Session Consolidation (protected long-term tier) -->
              @if (wm.consolidation_context) {
                <section class="section">
                  <h3>{{ 'brain.wm.consolidation' | translate }}</h3>
                  <div class="wm-consolidation-block">{{ wm.consolidation_context }}</div>
                </section>
              }
              <!-- Other workspace slots (cross-workspace view) -->
              @if (wmOtherSlots(); as other) {
                @if (other.slots.length) {
                  <section class="section">
                    <h3>{{ 'brain.wm.workspaceSlots' | translate:{ label: other.label, count: other.slots.length } }}</h3>
                    @for (s of other.slots; track $index) {
                      <div class="wm-card slot-card other-ws-card" [style.opacity]="Math.max(0.35, s.salience * 0.7)">
                        <span class="wm-card-badge">{{ s.type }}</span>
                        <span class="wm-card-content">{{ s.content }}</span>
                        @if (s.domain) { <span class="wm-card-domain muted">{{ s.domain }}</span> }
                      </div>
                    }
                  </section>
                }
              }
            }
            <!-- ANS Context Window -->
            @if (ansContext(); as ctx) {
              @if (ctx.items.length) {
                <section class="section">
                  <h3>{{ 'brain.wm.contextWindow' | translate:{ count: ctx.total } }}</h3>
                  <div class="ctx-list">
                    @for (item of ctx.items; track $index) {
                      <div class="wm-card ctx-card" [class]="'wm-card ctx-card ctx-' + item.signal_type.toLowerCase()">
                        <span class="wm-card-badge" [class]="'wm-card-badge badge-' + item.signal_type.toLowerCase()">{{ item.signal_type }}</span>
                        <span class="wm-card-content">{{ item.content }}</span>
                        @if (item.domain) { <span class="wm-card-domain muted">{{ item.domain }}</span> }
                      </div>
                    }
                  </div>
                </section>
              }
            }
            @if (!wmData() && !ansContext()?.items?.length) {
              <div class="empty-state">{{ 'brain.empty.wm' | translate }}</div>
            }
          </div>
        }

        <!-- ========== Theory of Mind ========== -->
        @if (activeTab() === 'theory-of-mind') {
          <div class="tab-panel">
            @if (tomData(); as tom) {
              <div class="tom-header">
                <span>{{ 'brain.tom.activeUser' | translate:{ user: tom.active_user || ('brain.tom.none' | translate) } }}</span>
                <span>{{ 'brain.tom.usersModeled' | translate:{ count: tom.user_count } }}</span>
              </div>
              @if (tom.temperature) {
                <section class="section">
                  <h3>{{ 'brain.tom.temperature' | translate }}</h3>
                  <div class="tom-temp-bar">
                    <div class="tom-temp-fill" [style.width.%]="tom.temperature.temperature * 100"></div>
                  </div>
                  <span class="tom-temp-label">{{ tom.temperature.label }} ({{ tom.temperature.temperature.toFixed(2) }})</span>
                </section>
              }
              @if (tom.users?.length) {
                <section class="section">
                  <h3>{{ 'brain.tom.userModels' | translate }}</h3>
                  @for (u of tom.users; track u.user_id) {
                    <div class="tom-user-card">
                      <div class="tom-user-header">
                        <span class="tom-user-id">{{ u.user_id }}</span>
                        <span class="tom-user-turns muted">{{ 'brain.tom.turns' | translate:{ count: u.turn_count } }}</span>
                      </div>
                      @if (u.style) { <div class="tom-field"><span class="tom-key">{{ 'brain.tom.style' | translate }}</span> {{ u.style }}</div> }
                      @if (u.patience) { <div class="tom-field"><span class="tom-key">{{ 'brain.tom.patience' | translate }}</span> {{ u.patience.toFixed(2) }}</div> }
                      @if (u.top_interests?.length) {
                        <div class="tom-interests">
                          @for (interest of u.top_interests; track interest) {
                            <span class="tom-interest-pill">{{ interest[0] || interest }}</span>
                          }
                        </div>
                      }
                      @if (u.expertise) {
                        <div class="tom-expertise">
                          @for (entry of (u.expertise | keyvalue); track entry.key) {
                            <div class="tom-expertise-row">
                              <span class="tom-exp-domain">{{ entry.key }}</span>
                              <div class="tom-exp-track"><div class="tom-exp-fill" [style.width.%]="(+entry.value) * 100"></div></div>
                            </div>
                          }
                        </div>
                      }
                    </div>
                  }
                </section>
              } @else if (tom.user_model) {
                <section class="section">
                  <h3>{{ 'brain.tom.activeUserModel' | translate }}</h3>
                  <div class="tom-user-card">
                    <div class="tom-user-header">
                      <span class="tom-user-id">{{ tom.user_model.user_id }}</span>
                      <span class="tom-user-turns muted">{{ 'brain.tom.turns' | translate:{ count: tom.user_model.turn_count } }}</span>
                    </div>
                    @if (tom.user_model.style) { <div class="tom-field"><span class="tom-key">{{ 'brain.tom.style' | translate }}</span> {{ tom.user_model.style }}</div> }
                  </div>
                </section>
              }
            } @else {
              <div class="empty-state">{{ 'brain.tom.empty' | translate }}</div>
            }
          </div>
        }

        <!-- ========== Narrative ========== -->
        @if (activeTab() === 'narrative') {
          <div class="tab-panel">
            @if (narrativeData(); as narr) {
              <!-- Header stats -->
              <div class="narrative-header">
                <div class="narr-stat">
                  <span class="narr-stat-value">{{ (narr.narrative_coherence * 100).toFixed(0) }}%</span>
                  <span class="narr-stat-label">{{ 'brain.narrative.coherence' | translate:{ label: narr.coherence_label } }}</span>
                </div>
                <div class="narr-stat">
                  <span class="narr-stat-value">{{ narr.episode_count }}</span>
                  <span class="narr-stat-label">{{ 'brain.narrative.episodes' | translate }}</span>
                </div>
                <div class="narr-stat">
                  <span class="narr-stat-value">{{ narr.regulation_count }}</span>
                  <span class="narr-stat-label">{{ 'brain.narrative.regulations' | translate }}</span>
                </div>
                @if (narr.active_strategy) {
                  <div class="narr-stat active-strategy">
                    <span class="narr-stat-value">{{ narr.active_strategy }}</span>
                    <span class="narr-stat-label">{{ 'brain.narrative.activeStrategy' | translate }}</span>
                  </div>
                }
              </div>
              <!-- Soul Wish -->
              @if (narr.soul_wish) {
                <div class="soul-wish-card">
                  <div class="soul-wish-label">{{ 'brain.narrative.soulWish' | translate }}</div>
                  <div class="soul-wish-text">{{ narr.soul_wish }}</div>
                </div>
              }

              @if (narr.values?.length) {
                <div class="narr-values">
                  <span class="narr-values-label">{{ 'brain.narrative.coreValues' | translate }}</span>
                  @for (v of narr.values; track v) {
                    <span class="narr-value-pill">{{ v }}</span>
                  }
                </div>
              }

              <!-- Narrative Thread -->
              @if (narr.narrative_blocks?.length) {
                <section class="section">
                  <h3>{{ 'brain.narrative.thread' | translate }}</h3>
                  <div class="narrative-timeline">
                    @for (block of (narr.narrative_blocks ?? []).slice().reverse().slice(0, narrativeBlocksExpanded ? 50 : 10); track block.timestamp) {
                      <div class="narrative-block">
                        <div class="nb-header">
                          <span class="nb-type-badge nb-type-{{ block.block_type }}">{{ block.block_type }}</span>
                          <span class="nb-time">{{ block.timestamp * 1000 | date:'short' }}</span>
                        </div>
                        <div class="nb-content">{{ block.content }}</div>
                        @if (block.domains?.length) {
                          <div class="nb-domains">
                            @for (d of block.domains; track d) {
                              <span class="domain-pill">{{ d }}</span>
                            }
                          </div>
                        }
                      </div>
                    }
                  </div>
                  @if ((narr.narrative_blocks?.length || 0) > 10 && !narrativeBlocksExpanded) {
                    <button class="show-more-btn" (click)="narrativeBlocksExpanded = true">
                      {{ 'brain.narrative.showAllBlocks' | translate:{ count: narr.narrative_blocks?.length } }}
                    </button>
                  }
                </section>
              }

              <!-- Current episode -->
              @if (narr.current_episode; as cur) {
                <section class="section">
                  <h3><span class="ep-live-dot"></span> {{ 'brain.narrative.liveEpisode' | translate }}</h3>
                  <div class="episode-card ep-current">
                    <div class="ep-header">
                      <span class="ep-number">#{{ cur.index }}</span>
                      <span class="ep-title">{{ cur.title }}</span>
                      <span class="ep-badge ep-active">{{ 'brain.narrative.live' | translate }}</span>
                    </div>
                    @if (cur.summary) {
                      <div class="ep-summary">{{ cur.summary }}</div>
                    }
                    <div class="ep-body">
                      <div class="ep-row"><span class="ep-key">{{ 'brain.narrative.turns' | translate }}</span><span>{{ cur.turns }}</span></div>
                      <div class="ep-row"><span class="ep-key">{{ 'brain.narrative.emotionalArc' | translate }}</span><span>{{ cur.arc_summary || ('brain.narrative.building' | translate) }}</span></div>
                      <div class="ep-row"><span class="ep-key">{{ 'brain.narrative.peakResonance' | translate }}</span><span>{{ cur.peak_resonance.toFixed(3) }}</span></div>
                      <div class="ep-row"><span class="ep-key">{{ 'brain.narrative.peakEngagement' | translate }}</span><span>{{ (cur.peak_engagement ?? 0).toFixed(3) }}</span></div>
                      @if (cur.start_time) {
                        <div class="ep-row"><span class="ep-key">{{ 'brain.narrative.started' | translate }}</span><span>{{ cur.start_time * 1000 | date:'short' }}</span></div>
                      }
                    </div>
                    @if (cur.topics?.length) {
                      <div class="ep-topics">
                        @for (t of cur.topics; track t) { <span class="topic-pill">{{ t }}</span> }
                      </div>
                    }
                    @if (cur.arc_snapshots?.length) {
                      <div class="ep-arc-visual">
                        <div class="ep-arc-label">{{ 'brain.narrative.moodJourney' | translate }}</div>
                        <div class="ep-arc-track">
                          @for (snap of cur.arc_snapshots; track snap.turn) {
                            <div class="ep-arc-dot" [title]="moodArcTitle(snap)"
                                 [style.background]="moodColor(snap.v)"
                                 [style.bottom.%]="(snap.v + 1) * 50">
                            </div>
                          }
                        </div>
                        <div class="ep-arc-moods">
                          @for (snap of cur.arc_snapshots; track snap.turn) {
                            <span class="ep-arc-mood-label" [style.color]="moodColor(snap.v)">{{ snap.mood }}</span>
                          }
                        </div>
                      </div>
                    }
                    @if (cur.domains?.length) {
                      <div class="ep-domains">
                        @for (d of cur.domains; track d) { <span class="domain-pill">{{ d }}</span> }
                      </div>
                    }
                  </div>
                </section>
              }

              <!-- Past episodes -->
              @if (narr.episodes?.length) {
                <section class="section">
                  <h3>{{ 'brain.narrative.episodeHistory' | translate }}</h3>
                  @for (ep of narr.episodes.slice().reverse(); track ep.index) {
                    <div class="episode-card ep-closed" [class.ep-expanded]="expandedEpisode === ep.index"
                         (click)="expandedEpisode = expandedEpisode === ep.index ? null : ep.index">
                      <div class="ep-header">
                        <span class="ep-number">#{{ ep.index }}</span>
                        <span class="ep-title">{{ ep.title }}</span>
                        <span class="ep-meta-inline">
                          {{ 'brain.narrative.turnsMeta' | translate:{ count: ep.turns } }}
                          @if (ep.duration_min) { &bull; {{ 'brain.narrative.durationMin' | translate:{ min: (ep.duration_min | number:'1.0-0') } } }
                        </span>
                        <span class="ep-mood-badge" [style.background]="moodColor(moodValence(ep.dominant_emotion))">{{ ep.dominant_emotion }}</span>
                        <span class="ep-chevron">{{ expandedEpisode === ep.index ? '▾' : '▸' }}</span>
                      </div>

                      @if (expandedEpisode === ep.index) {
                        <div class="ep-body" (click)="$event.stopPropagation()">
                          @if (ep.summary) {
                            <div class="ep-summary">{{ ep.summary }}</div>
                          }
                          <div class="ep-row"><span class="ep-key">{{ 'brain.narrative.emotionalArc' | translate }}</span><span>{{ ep.arc_summary }}</span></div>
                          <div class="ep-row"><span class="ep-key">{{ 'brain.narrative.moodJourney' | translate }}</span><span>{{ ep.opening_mood }} → {{ ep.closing_mood }}</span></div>
                          <div class="ep-row"><span class="ep-key">{{ 'brain.narrative.dominantEmotion' | translate }}</span><span>{{ ep.dominant_emotion }}</span></div>
                          <div class="ep-row"><span class="ep-key">{{ 'brain.narrative.peakResonance' | translate }}</span><span>{{ ep.peak_resonance.toFixed(3) }}</span></div>
                          <div class="ep-row"><span class="ep-key">{{ 'brain.narrative.peakEngagement' | translate }}</span><span>{{ (ep.peak_engagement ?? 0).toFixed(3) }}</span></div>
                          <div class="ep-row"><span class="ep-key">{{ 'brain.narrative.peakCortisol' | translate }}</span><span>{{ (ep.peak_cortisol ?? 0).toFixed(3) }}</span></div>
                          @if (ep.coherence_contribution) {
                            <div class="ep-row"><span class="ep-key">{{ 'brain.narrative.coherenceContribution' | translate }}</span><span>{{ (ep.coherence_contribution * 100).toFixed(0) }}%</span></div>
                          }
                          @if (ep.start_time) {
                            <div class="ep-row"><span class="ep-key">{{ 'brain.narrative.started' | translate }}</span><span>{{ ep.start_time * 1000 | date:'medium' }}</span></div>
                          }
                          @if (ep.end_time) {
                            <div class="ep-row"><span class="ep-key">{{ 'brain.narrative.ended' | translate }}</span><span>{{ ep.end_time * 1000 | date:'medium' }}</span></div>
                          }
                          @if (ep.topics?.length) {
                            <div class="ep-topics">
                              <span class="ep-topics-label">{{ 'brain.narrative.topics' | translate }}</span>
                              @for (t of ep.topics; track t) { <span class="topic-pill">{{ t }}</span> }
                            </div>
                          }
                          @if (ep.domains?.length) {
                            <div class="ep-domains">
                              @for (d of ep.domains; track d) { <span class="domain-pill">{{ d }}</span> }
                            </div>
                          }
                          @if (ep.arc_snapshots?.length) {
                            <div class="ep-arc-visual">
                              <div class="ep-arc-label">{{ 'brain.narrative.turnByTurnMood' | translate }}</div>
                              <div class="ep-arc-track">
                                @for (snap of ep.arc_snapshots; track snap.turn) {
                                  <div class="ep-arc-dot" [title]="moodArcTitle(snap)"
                                       [style.background]="moodColor(snap.v)"
                                       [style.bottom.%]="(snap.v + 1) * 50">
                                  </div>
                                }
                              </div>
                              <div class="ep-arc-moods">
                                @for (snap of ep.arc_snapshots; track snap.turn) {
                                  <span class="ep-arc-mood-label" [style.color]="moodColor(snap.v)">{{ snap.mood }}</span>
                                }
                              </div>
                            </div>
                          }
                        </div>
                      }
                    </div>
                  }
                </section>
              }

              @if (!narr.current_episode && !narr.episodes?.length) {
                <div class="empty-state">{{ 'brain.narrative.emptyEpisodes' | translate }}</div>
              }
            } @else {
              <div class="empty-state">{{ 'brain.narrative.empty' | translate }}</div>
            }
          </div>
        }

        <!-- ========== Predictions ========== -->
        @if (activeTab() === 'predictions') {
          <div class="tab-panel">
            @if (status()?.predictive_processing; as pp) {
              <div class="predictions-header">
                <span>{{ 'brain.predictions.headerPredictions' | translate:{ count: pp.prediction_count } }}</span>
                <span>{{ 'brain.predictions.headerAvgPe' | translate:{ value: pp.average_pe.toFixed(3) } }}</span>
                <span>{{ 'brain.predictions.headerSurprises' | translate:{ count: pp.surprise_count } }}</span>
              </div>
              @if (pp.last_prediction) {
                <section class="section">
                  <h3>{{ 'brain.predictions.lastPrediction' | translate }}</h3>
                  <div class="prediction-detail">
                    <div class="pred-row"><span class="pred-key">{{ 'brain.predictions.turn' | translate }}</span><span>{{ pp.last_prediction.turn }}</span></div>
                    <div class="pred-row"><span class="pred-key">{{ 'brain.predictions.pe' | translate }}</span><span>{{ pp.last_prediction.pe.toFixed(3) }}</span></div>
                    <div class="pred-row"><span class="pred-key">{{ 'brain.predictions.confidence' | translate }}</span><span>{{ pp.last_prediction.confidence.toFixed(2) }}</span></div>
                    <div class="pred-row"><span class="pred-key">{{ 'brain.predictions.expected' | translate }}</span><span class="mono">{{ pp.last_prediction.expected_domain }}</span></div>
                    <div class="pred-row"><span class="pred-key">{{ 'brain.predictions.actual' | translate }}</span><span class="mono">{{ pp.last_prediction.actual_domain }}</span></div>
                  </div>
                </section>
              }
              @if (pp.high_uncertainty_domains?.length) {
                <section class="section">
                  <h3>{{ 'brain.predictions.highUncertainty' | translate }}</h3>
                  @for (d of pp.high_uncertainty_domains; track d.domain) {
                    <div class="uncertainty-row">
                      <span class="uncertainty-domain mono">{{ d.domain }}</span>
                      <div class="uncertainty-track"><div class="uncertainty-fill" [style.width.%]="d.uncertainty * 100"></div></div>
                      <span class="uncertainty-val">{{ d.uncertainty.toFixed(3) }}</span>
                    </div>
                  }
                </section>
              }
            } @else {
              <div class="empty-state">{{ 'brain.predictions.empty' | translate }}</div>
            }
          </div>
        }

        <!-- ========== Config ========== -->
        @if (activeTab() === 'config') {
          <div class="tab-panel">
            @if (config(); as cfg) {
              <div class="config-toolbar">
                <button class="view-toggle-btn" (click)="toggleConfigView()">
                  {{ configViewMode() === 'visual' ? ('brain.config.showJson' | translate) : ('brain.config.showCards' | translate) }}
                </button>
              </div>

              @if (configViewMode() === 'visual') {
                <!-- VISUAL CARD VIEW -->
                <div class="config-cards">
                  @for (key of configKeys(); track key) {
                    <details class="config-card">
                      <summary class="config-card-header">
                        <span class="config-card-icon">{{ configSectionIcon(key) }}</span>
                        <div class="config-card-title-group">
                          <span class="config-card-title">{{ key }}</span>
                          @if (configSectionVersion(cfg[key]); as ver) {
                            <span class="config-card-version">v{{ ver }}</span>
                          }
                        </div>
                        @if (configSectionDescription(cfg[key]); as desc) {
                          <span class="config-card-desc">{{ desc.length > 100 ? desc.slice(0, 100) + '…' : desc }}</span>
                        }
                      </summary>
                      <div class="config-card-body">
                        @if (configSectionDescription(cfg[key]); as desc) {
                          <p class="config-card-full-desc">{{ desc }}</p>
                        }
                        <div class="config-field-grid">
                          @for (field of configSectionSummary(key, cfg[key]); track field.label) {
                            <div class="config-field">
                              <span class="config-field-label">{{ field.label }}</span>
                              @if (field.type === 'bool') {
                                <span class="config-field-value config-bool" [class.config-bool-on]="field.value === 'true'" [class.config-bool-off]="field.value === 'false'">
                                  {{ field.value === 'true' ? ('brain.config.on' | translate) : ('brain.config.off' | translate) }}
                                </span>
                              } @else if (field.type === 'number') {
                                <span class="config-field-value config-number">{{ field.value }}</span>
                              } @else {
                                <span class="config-field-value">{{ field.value }}</span>
                              }
                            </div>
                          }
                        </div>
                        <details class="config-json-toggle">
                          <summary class="config-json-summary">{{ 'brain.config.rawJson' | translate }}</summary>
                          <div class="json-tree-container">
                            <pre class="config-code"><code>{{ formatJson(cfg[key]) }}</code></pre>
                          </div>
                        </details>
                      </div>
                    </details>
                  }
                </div>
              } @else {
                <!-- FULL JSON VIEW -->
                @for (key of configKeys(); track key) {
                  <section class="config-section">
                    <h4 class="config-title">{{ key }}</h4>
                    <pre class="config-code"><code>{{ formatJson(cfg[key]) }}</code></pre>
                  </section>
                }
              }

              @if (!configKeys().length) {
                <div class="empty-state">{{ 'brain.config.empty' | translate }}</div>
              }
            } @else {
              <div class="empty-state">{{ 'brain.config.loading' | translate }}</div>
            }
          </div>
        }
      </main>

    </div>
  `,
})
export class BrainComponent implements OnInit, OnDestroy {
  private readonly translate = inject(TranslateService);

  Math = Math;
  private destroy$ = new Subject<void>();
  private agentId = '';

  agent = signal<Agent | null>(null);
  status = signal<AgentRuntimeStatus | null>(null);
  chain = signal<ChainState | null>(null);
  hormoneHistory = signal<HormoneHistory | null>(null);
  networkHistory = signal<NetworkHistory | null>(null);
  signalHistory = signal<SignalHistory | null>(null);
  eventsData = signal<EventsResponse | null>(null);
  config = signal<Record<string, any> | null>(null);

  activeTab = signal<string>('overview');
  sleeping = signal(false);
  eventFilter = '';
  expandedEvent = signal<string | null>(null);

  // Config visual mode
  configViewMode = signal<'visual' | 'json'>('visual');

  // Narrative episode expansion
  expandedEpisode: number | null = null;
  narrativeBlocksExpanded = false;

  tabs = [
    // State group
    { id: 'overview', label: 'Overview', group: 'state' },
    { id: 'self-state', label: 'Self-State', group: 'state' },
    { id: 'network', label: 'Network', group: 'state' },
    { id: 'hormones', label: 'Hormones', group: 'state' },
    // Cognition group
    { id: 'working-memory', label: 'Working Memory', group: 'cognition' },
    { id: 'theory-of-mind', label: 'Theory of Mind', group: 'cognition' },
    { id: 'narrative', label: 'Narrative', group: 'cognition' },
    { id: 'predictions', label: 'Predictions', group: 'cognition' },
    // Perception group
    { id: 'visual-cortex', label: 'Visual Cortex', group: 'perception' },
    // System group
    { id: 'signals', label: 'Signals', group: 'system' },
    { id: 'events', label: 'Events', group: 'system' },
    { id: 'schedule', label: 'Schedule', group: 'system' },
    { id: 'config', label: 'Config', group: 'system' },
  ];

  // Front-brain data signals
  wmData = signal<WorkingMemoryStatus | null>(null);
  wmViewWorkspace = signal<'professional' | 'personal' | null>(null);
  wmActiveWorkspace = computed(() => this.wmData()?.active_workspace || 'professional');
  wmDisplayWorkspace = computed(() => this.wmViewWorkspace() ?? this.wmActiveWorkspace());
  narrativeData = signal<NarrativeStatus | null>(null);
  tomData = signal<TheoryOfMindStatus | null>(null);
  networkData = signal<NetworkDynamicsStatus | null>(null);

  // ANS context (displayed alongside front-brain WM)
  ansContext = signal<{ items: any[]; total: number } | null>(null);

  wmPrimarySlots = computed(() => {
    const wm = this.wmData();
    if (!wm) return [];
    const viewWs = this.wmDisplayWorkspace();
    const activeWs = wm.active_workspace || 'professional';
    if (viewWs === activeWs) return wm.slots || [];
    return ((wm as any)[`${viewWs}_slots`] ?? []) as typeof wm.slots;
  });
  wmPrimaryGoals = computed(() => {
    const wm = this.wmData();
    if (!wm) return [];
    const viewWs = this.wmDisplayWorkspace();
    const activeWs = wm.active_workspace || 'professional';
    if (viewWs === activeWs) return wm.goals || [];
    return ((wm as any)[`${viewWs}_goals`] ?? []) as typeof wm.goals;
  });
  wmOtherSlots = computed(() => {
    const wm = this.wmData();
    if (!wm) return { label: '', slots: [] };
    const viewWs = this.wmDisplayWorkspace();
    const otherWs = viewWs === 'professional' ? 'personal' : 'professional';
    const activeWs = wm.active_workspace || 'professional';
    const otherSlots = viewWs === activeWs
      ? ((wm as any)[`${otherWs}_slots`] ?? [])
      : (wm.slots || []);
    return {
      label: this.translate.instant('brain.wm.' + otherWs),
      slots: otherSlots as { type: string; content: string; salience: number; domain: string }[],
    };
  });

  // Real-time neural probe activity
  probeSignals = signal<{
    signals: Record<string, number>;
    fired: string[];
    iteration: number;
    midGeneration: boolean;
    ts: Date;
  } | null>(null);
  probeHistory = signal<{ signals: Record<string, number>; ts: Date }[]>([]);

  // Visual Cortex buffer
  vcData = signal<any>(null);
  vcChannelFilter = signal<string>('');
  vcAutoRefresh = signal(false);
  private vcRefreshTimer: any = null;

  // Schedule form
  scheduleBedtime = '';
  scheduleWakeTime = '';
  scheduleTimezone = '';
  scheduleWakeOnMessage = true;
  scheduleNapStart = '';
  scheduleNapEnd = '';
  scheduleSaving = signal(false);
  scheduleSaved = signal(false);

  displayStatus = computed(() => {
    const s = this.status();
    const a = this.agent();
    return s?.status ?? a?.status ?? 'unknown';
  });

  ansState = computed(() => {
    const s = this.status();
    return s?.ans?.state ?? 'unknown';
  });

  hormoneList = computed(() => {
    const s = this.status();
    const hormones = s?.hormones ?? {};
    const colors: Record<string, string> = {
      dopamine: 'var(--accent-success)',
      serotonin: 'var(--accent-primary)',
      norepinephrine: 'var(--accent-warn)',
      cortisol: 'var(--accent-danger)',
      oxytocin: 'var(--accent-primary)',
    };
    return Object.entries(hormones).map(([key, value]) => ({
      key,
      label: this.translate.instant('info.hormones.legend.' + key),
      value: typeof value === 'number' ? Math.min(1, Math.max(0, value)) : 0,
      color: colors[key] ?? 'var(--accent-primary)',
    }));
  });

  sortedProbeSignals = computed(() => {
    const ps = this.probeSignals();
    if (!ps) return [];
    return Object.entries(ps.signals)
      .map(([key, value]) => ({ key, value }))
      .sort((a, b) => b.value - a.value);
  });

  signalTypeCounts = computed(() => {
    const sh = this.signalHistory();
    const counts = sh?.type_counts ?? {};
    return Object.entries(counts).map(([type, count]) => ({ type, count }));
  });

  recentSignals = computed(() => {
    const sh = this.signalHistory();
    const signals = sh?.signals ?? [];
    return signals.slice(0, 50);
  });

  events = computed(() => {
    const ed = this.eventsData();
    return ed?.events ?? [];
  });

  eventTypes = computed(() => {
    const ed = this.eventsData();
    const types = new Set<string>();
    (ed?.events ?? []).forEach((e) => types.add(e.event));
    return Array.from(types).sort();
  });

  hormoneChartSeries = computed<LineChartSeries[]>(() => {
    const hh = this.hormoneHistory();
    if (!hh?.hormones) return [];
    const colors: Record<string, string> = {
      dopamine: 'var(--accent-success)', serotonin: 'var(--accent-primary)',
      norepinephrine: 'var(--accent-warn)', cortisol: 'var(--accent-danger)', oxytocin: 'var(--accent-primary)',
    };
    return Object.entries(hh.hormones)
      .filter(([, pts]) => pts.length > 1)
      .map(([key, pts]) => ({
        label: this.translate.instant('info.hormones.legend.' + key),
        color: colors[key] ?? '#94a3b8',
        data: pts.map(p => ({ x: p.turn, y: p.value })),
      }));
  });

  networkChartSeries = computed<LineChartSeries[]>(() => {
    const nh = this.networkHistory();
    if (!nh?.network) return [];
    const colors: Record<string, string> = {
      ecn: 'var(--accent-primary)', sn: 'var(--accent-warn)', dmn: 'var(--accent-primary)',
    };
    const labels: Record<string, string> = {
      ecn: this.translate.instant('brain.networkDetail.chartEcn'),
      sn: this.translate.instant('brain.networkDetail.chartSn'),
      dmn: this.translate.instant('brain.networkDetail.chartDmn'),
    };
    return Object.entries(nh.network)
      .filter(([, pts]) => pts.length > 1)
      .map(([key, pts]) => ({
        label: labels[key] ?? key,
        color: colors[key] ?? '#94a3b8',
        data: pts.map(p => ({ x: p.turn, y: p.value })),
      }));
  });

  constructor(
    private route: ActivatedRoute,
    private api: ApiService,
    private wsService: WebSocketService,
  ) {}

  ngOnInit(): void {
    this.agentId = this.route.snapshot.params['agentId'] ?? '';

    this.api
      .getAgent(this.agentId)
      .pipe(takeUntil(this.destroy$))
      .subscribe({
        next: (agent) => {
          this.agent.set(agent);
          this.loadRuntimeData();
        },
      });

    this.wsService.onProbeSignal()
      .pipe(takeUntil(this.destroy$))
      .subscribe(data => {
        this.probeSignals.set(data);
        this.probeHistory.update(h => {
          const updated = [...h, { signals: data.signals, ts: data.ts }];
          return updated.slice(-30);
        });
      });
  }

  ngOnDestroy(): void {
    this.destroy$.next();
    this.destroy$.complete();
    if (this.vcRefreshTimer) {
      clearInterval(this.vcRefreshTimer);
      this.vcRefreshTimer = null;
    }
  }

  private loadRuntimeData(): void {
    if (!this.agentId) return;

    this.api.getAgentStatus(this.agentId).pipe(takeUntil(this.destroy$)).subscribe({
      next: (s) => this.status.update((prev) => {
        if (!prev) return s;
        const ph = prev.heartbeat;
        const sh = s.heartbeat;
        if (ph && typeof ph === 'object') {
          const mergedHb =
            sh && typeof sh === 'object' ? { ...ph, ...sh } : { ...ph };
          return { ...s, heartbeat: mergedHb };
        }
        return s;
      }),
    });

    this.api.getAgentChain(this.agentId).pipe(takeUntil(this.destroy$)).subscribe({
      next: (c) => this.chain.set(c),
    });

    this.api.getHormoneHistory(this.agentId).pipe(takeUntil(this.destroy$)).subscribe({
      next: (h) => this.hormoneHistory.set(h),
    });

    this.api.getNetworkHistory(this.agentId).pipe(takeUntil(this.destroy$)).subscribe({
      next: (h) => this.networkHistory.set(h),
    });

    this.api.getSignalHistory(this.agentId).pipe(takeUntil(this.destroy$)).subscribe({
      next: (s) => this.signalHistory.set(s),
    });

    this.loadEvents();
    this.loadConfig();
    this.loadVisualCortex();
    this._loadFrontBrainData();
  }

  private _loadFrontBrainData(): void {
    if (!this.agentId) return;
    this.api.getWorkingMemory(this.agentId).pipe(takeUntil(this.destroy$)).subscribe({
      next: (d) => this.wmData.set(d),
      error: () => this.wmData.set(null),
    });
    this.api.getAnsContext(this.agentId).pipe(takeUntil(this.destroy$)).subscribe({
      next: (d) => this.ansContext.set(d),
      error: () => this.ansContext.set(null),
    });
    this.api.getNarrativeEpisodes(this.agentId).pipe(takeUntil(this.destroy$)).subscribe({
      next: (d) => this.narrativeData.set(d),
      error: () => this.narrativeData.set(null),
    });
    this.api.getTheoryOfMind(this.agentId).pipe(takeUntil(this.destroy$)).subscribe({
      next: (d) => this.tomData.set(d),
      error: () => this.tomData.set(null),
    });
    this.api.getNetworkDynamics(this.agentId).pipe(takeUntil(this.destroy$)).subscribe({
      next: (d) => this.networkData.set(d),
      error: () => this.networkData.set(null),
    });
  }

  loadEvents(): void {
    if (!this.agentId) return;

    const opts: { event_type?: string; limit?: number } = { limit: 100 };
    if (this.eventFilter) opts.event_type = this.eventFilter;

    this.api.getAgentEvents(this.agentId, opts).pipe(takeUntil(this.destroy$)).subscribe({
      next: (r) => this.eventsData.set(r),
    });
  }

  loadVisualCortex(): void {
    if (!this.agentId) return;
    const opts: { channel?: string; limit?: number } = { limit: 100 };
    const ch = this.vcChannelFilter();
    if (ch) opts.channel = ch;
    this.api.getVisualCortexBuffer(this.agentId, opts).pipe(takeUntil(this.destroy$)).subscribe({
      next: (d) => this.vcData.set(d),
      error: () => this.vcData.set(null),
    });
  }

  toggleVcAutoRefresh(): void {
    const next = !this.vcAutoRefresh();
    this.vcAutoRefresh.set(next);
    if (next) {
      this.vcRefreshTimer = setInterval(() => this.loadVisualCortex(), 3000);
    } else if (this.vcRefreshTimer) {
      clearInterval(this.vcRefreshTimer);
      this.vcRefreshTimer = null;
    }
  }

  loadConfig(): void {
    if (!this.agentId) return;
    this.api.getAgentConfig(this.agentId).pipe(takeUntil(this.destroy$)).subscribe({
      next: (c) => {
        this.config.set(c);
        this._populateScheduleFromConfig(c);
      },
    });
  }

  private _populateScheduleFromConfig(cfg: Record<string, any>): void {
    const circ = cfg?.['autonomic']?.['circadian'] ?? {};
    this.scheduleBedtime = circ['bedtime'] ?? '02:00';
    this.scheduleWakeTime = circ['wake_time'] ?? '07:00';
    this.scheduleTimezone = circ['timezone'] ?? 'UTC';
    this.scheduleWakeOnMessage = circ['wake_on_user_message'] ?? true;
    const naps = circ['nap_windows'] ?? [];
    if (naps.length > 0) {
      this.scheduleNapStart = naps[0]['start'] ?? '';
      this.scheduleNapEnd = naps[0]['end'] ?? '';
    }
  }

  saveSchedule(): void {
    if (!this.agentId || this.scheduleSaving()) return;
    this.scheduleSaving.set(true);
    this.scheduleSaved.set(false);

    const payload: Record<string, any> = {
      bedtime: this.scheduleBedtime,
      wake_time: this.scheduleWakeTime,
      timezone: this.scheduleTimezone,
      wake_on_user_message: this.scheduleWakeOnMessage,
    };
    if (this.scheduleNapStart && this.scheduleNapEnd) {
      payload['nap_windows'] = [
        { start: this.scheduleNapStart, end: this.scheduleNapEnd, condition: 'signal_pressure' },
      ];
    } else {
      payload['nap_windows'] = [];
    }

    this.api.updateCircadianConfig(this.agentId, payload)
      .pipe(takeUntil(this.destroy$))
      .subscribe({
        next: () => {
          this.scheduleSaving.set(false);
          this.scheduleSaved.set(true);
          setTimeout(() => this.scheduleSaved.set(false), 3000);
        },
        error: () => this.scheduleSaving.set(false),
      });
  }

  toggleEvent(ts: string): void {
    this.expandedEvent.update((current) => (current === ts ? null : ts));
  }

  onSleep(): void {
    if (!this.agentId || this.sleeping()) return;

    this.sleeping.set(true);
    this.api.forceSleep(this.agentId).subscribe({
      next: () => {
        this.sleeping.set(false);
        this.loadRuntimeData();
      },
      error: () => this.sleeping.set(false),
    });
  }

  formatJson(obj: any): string {
    try {
      return JSON.stringify(obj, null, 2);
    } catch {
      return String(obj);
    }
  }

  // ===========================================================
  // Narrative helpers
  // ===========================================================

  /** Map a valence value (-1..1) to a color from red through neutral to green */
  moodColor(valence: number): string {
    if (valence >= 0.3) return 'var(--accent-success)';
    if (valence >= 0.1) return 'var(--accent-success)';
    if (valence >= -0.1) return '#94a3b8';
    if (valence >= -0.3) return 'var(--accent-warn)';
    return 'var(--accent-danger)';
  }

  /** Rough valence estimate from a mood label */
  moodValence(mood: string | undefined): number {
    if (!mood) return 0;
    const m = mood.toLowerCase();
    const map: Record<string, number> = {
      warm: 0.5, serene: 0.4, curious: 0.3, aligned: 0.4,
      playful: 0.3, engaged: 0.2, focused: 0.1,
      neutral: 0, calm: 0.1,
      tense: -0.3, anxious: -0.4, frustrated: -0.5,
      conflicted: -0.2,
    };
    return map[m] ?? 0;
  }

  // ===========================================================
  // Config visual helpers
  // ===========================================================

  toggleConfigView(): void {
    this.configViewMode.set(this.configViewMode() === 'visual' ? 'json' : 'visual');
  }

  configKeys(): string[] {
    const cfg = this.config();
    return cfg ? Object.keys(cfg) : [];
  }

  configSectionSummary(key: string, data: any): { label: string; value: string; type: 'text' | 'bool' | 'number' }[] {
    if (!data || typeof data !== 'object') return [];
    const items: { label: string; value: string; type: 'text' | 'bool' | 'number' }[] = [];
    const walk = (obj: any, prefix: string, depth: number) => {
      if (depth > 2 || items.length > 12) return;
      for (const [k, v] of Object.entries(obj)) {
        if (k === 'description' || k === 'version') continue;
        const label = prefix ? `${prefix}.${k}` : k;
        if (typeof v === 'boolean') {
          items.push({ label, value: String(v), type: 'bool' });
        } else if (typeof v === 'number') {
          items.push({ label, value: String(v), type: 'number' });
        } else if (typeof v === 'string' && v.length < 60 && !v.includes('\n')) {
          items.push({ label, value: v, type: 'text' });
        } else if (typeof v === 'object' && v !== null && !Array.isArray(v) && depth < 2) {
          walk(v, label, depth + 1);
        }
      }
    };
    walk(data, '', 0);
    return items.slice(0, 12);
  }

  configSectionDescription(data: any): string {
    return data?.description || '';
  }

  configSectionVersion(data: any): string {
    return data?.version || '';
  }

  moodArcTitle(snap: { turn: number; mood: string; v: number; a: number }): string {
    return this.translate.instant('brain.narrative.moodArcTitle', {
      turn: snap.turn,
      mood: snap.mood,
      v: snap.v,
      a: snap.a,
    });
  }

  configSectionIcon(key: string): string {
    const icons: Record<string, string> = {
      runtime: '🧠', hormones: '💉', autonomic: '🫀', drives: '🔥',
      dmn: '💭', signals: '📡', calibration: '🎯',
    };
    return icons[key] || '⚙️';
  }
}
