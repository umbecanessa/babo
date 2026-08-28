import { Component, OnInit, signal, computed } from '@angular/core';
import { CommonModule, KeyValuePipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';
import { ApiService } from '../../core/services/api.service';
import { StatusBadgeComponent } from '../../shared/components/status-badge.component';
import { TimeAgoPipe } from '../../shared/pipes/time-ago.pipe';
import {
  Agent,
  ChainState,
  Block,
  Fact,
  FactsResponse,
  ConversationMessage,
  ForkResult,
  SoulImportResult,
  NarrativeStatus,
  WorkingMemoryStatus,
  WMSlot,
} from '../../core/models/agent.model';
import {
  parseTags as _parseTags,
  tagColor as _tagColor,
  humanType as _humanType,
  SignalTag,
} from '../../shared/signal-utils';

type MemTab = 'overview' | 'knowledge' | 'chain' | 'working-memory' | 'episodes' | 'soul';

interface TreeNode {
  key: string;
  path: string;
  depth: number;
  childCount: number;
  factCount: number;
  facts: Fact[];
  totalFacts: number;
}

interface ContextItem {
  index: number;
  signal_type: string;
  domain: string;
  content: string;
  source: string;
  timestamp: string;
}

interface ContextGroup {
  type: string;
  label: string;
  color: string;
  items: ContextItem[];
}

import { TranslateModule } from '@ngx-translate/core';

@Component({
  selector: 'app-memory',
  standalone: true,
  imports: [CommonModule, FormsModule, KeyValuePipe, StatusBadgeComponent, TimeAgoPipe, TranslateModule],
  template: `
    @if (loading()) {
      <div class="loading-state">
        <div class="spinner"></div>
        <span>{{ 'memory.loading' | translate }}</span>
      </div>
    }

    @if (agent(); as a) {
      <header class="detail-header">
        <div class="header-info">
          <div class="header-title-row">
            <h1 class="agent-name">{{ a.name || ('memory.unnamed' | translate) }}</h1>
            <app-status-badge [status]="a.status" />
          </div>
          <div class="header-meta">
            <span class="meta-item">
              <span class="meta-label">{{ 'memory.runtimeId' | translate }}</span>
              <code class="mono">{{ a.runtimeAgentId }}</code>
            </span>
          </div>
        </div>
      </header>

      <nav class="tab-bar">
        @for (tab of tabs; track tab.id) {
          <button
            class="tab-item"
            [class.active]="activeTab() === tab.id"
            (click)="selectTab(tab.id)"
          >
            <span class="tab-icon">{{ tab.icon }}</span>
            {{ ('memory.tabs.' + tab.id) | translate }}
          </button>
        }
      </nav>

      <div class="tab-panel">
        @switch (activeTab()) {

          <!-- ===================== OVERVIEW ===================== -->
          @case ('overview') {
            <div class="overview-grid">
              <div class="stat-card clickable" (click)="selectTab('knowledge')">
                <span class="stat-value">{{ overviewStats().factsCount }}</span>
                <span class="stat-label">{{ 'memory.stats.facts' | translate }}</span>
              </div>
              <div class="stat-card clickable" (click)="selectTab('chain')">
                <span class="stat-value">{{ overviewStats().chainHeight }}</span>
                <span class="stat-label">{{ 'memory.stats.chainHeight' | translate }}</span>
              </div>
              <div class="stat-card">
                <span class="stat-value">{{ overviewStats().sleepCount }}</span>
                <span class="stat-label">{{ 'memory.stats.sleeps' | translate }}</span>
              </div>
              <div class="stat-card clickable" (click)="selectTab('working-memory')">
                <span class="stat-value">{{ wmData()?.slot_count ?? overviewStats().ansSignals }}</span>
                <span class="stat-label">{{ wmData()?.slot_count != null ? ('memory.stats.wmSlots' | translate) : ('memory.stats.ansSignals' | translate) }}</span>
              </div>
              <div class="stat-card">
                <span class="stat-value stat-mono">{{ overviewStats().sovereignty }}</span>
                <span class="stat-label">{{ 'memory.stats.sovereignty' | translate }}</span>
              </div>
              <div class="stat-card clickable" (click)="selectTab('soul')">
                <span class="stat-value stat-mono">{{ truncate(overviewStats().soulHash, 14) }}</span>
                <span class="stat-label">{{ 'memory.stats.soulHash' | translate }}</span>
              </div>
            </div>

            <!-- Mini chain preview -->
            @if (chainState(); as cs) {
              <div class="overview-section">
                <h3 class="section-heading section-heading-link" (click)="selectTab('chain')">Recent Chain Blocks <span class="heading-arrow">→</span></h3>
                <div class="mini-chain">
                  @for (block of recentBlocks(); track block.height) {
                    <div
                      class="mini-block"
                      [class.epoch]="block.block_type === 'epoch'"
                      [class.delta]="block.block_type === 'delta'"
                      [class.expanded]="overviewExpandedBlock() === block.height"
                      (click)="toggleOverviewBlock(block.height)"
                    >
                      <span class="mb-height">{{ block.height }}</span>
                      <span class="mb-type">{{ block.block_type }}</span>
                      <span class="mb-aku">{{ block.aku_count }} AKU</span>
                      <span class="mb-ts">{{ block.timestamp | timeAgo }}</span>
                      @if (overviewExpandedBlock() === block.height) {
                        <div class="mb-details">
                          <div class="mb-detail-row"><span class="mb-detail-label">Hash</span><code>{{ truncate(block.block_hash, 20) }}</code></div>
                          <div class="mb-detail-row"><span class="mb-detail-label">Parent</span><code>{{ truncate(block.parent_hash, 20) }}</code></div>
                          <div class="mb-detail-row"><span class="mb-detail-label">Delta</span><code>{{ block.delta_path || '—' }}</code></div>
                        </div>
                      }
                    </div>
                    @if (!$last) {
                      <div class="mini-chain-link"></div>
                    }
                  }
                  @if (recentBlocks().length === 0) {
                    <div class="empty-state-sm">No blocks yet</div>
                  }
                </div>
              </div>
            }

            <!-- Recent facts -->
            @if (facts(); as f) {
              <div class="overview-section">
                <h3 class="section-heading section-heading-link" (click)="selectTab('knowledge')">Recent Facts <span class="heading-arrow">→</span></h3>
                <div class="recent-facts">
                  @for (fact of recentFacts(); track fact.id) {
                    <div class="rf-item" [class.rf-expanded]="overviewExpandedFact() === fact.id" (click)="toggleOverviewFact(fact.id)">
                      <div class="rf-summary">
                        <span class="rf-dot" [style.background]="domainColor(fact.domain_path)"></span>
                        <span class="rf-domain">{{ lastSegment(fact.domain_path) }}</span>
                        <span class="rf-value">{{ truncate(fact.current_value, 60) }}</span>
                        <span class="rf-ts">{{ fact.last_modified | timeAgo }}</span>
                      </div>
                      @if (overviewExpandedFact() === fact.id) {
                        <div class="rf-detail">
                          <div class="rf-detail-full">{{ fact.current_value }}</div>
                          <div class="rf-detail-meta">
                            <span><strong>Domain:</strong> {{ fact.domain_path }}</span>
                            <span><strong>Question:</strong> {{ fact.canonical_question || '—' }}</span>
                            <span><strong>Block:</strong> {{ fact.block_height }}</span>
                            <span><strong>Flips:</strong> {{ fact.flip_count }}</span>
                            @if (fact.is_fluid) {
                              <span class="fluid-badge">fluid</span>
                            }
                          </div>
                        </div>
                      }
                    </div>
                  }
                  @if (recentFacts().length === 0) {
                    <div class="empty-state-sm">No facts stored yet</div>
                  }
                </div>
              </div>
            }

            <!-- Working Memory summary -->
            <div class="overview-section">
              <h3 class="section-heading section-heading-link" (click)="selectTab('working-memory')">Working Memory <span class="heading-arrow">→</span></h3>
              <div class="wm-summary">
                @for (group of wmGrouped(); track group.type) {
                  <div class="wm-summary-pill clickable" [style.border-color]="group.color" (click)="selectTab('working-memory')">
                    <span class="wm-pill-dot" [style.background]="group.color"></span>
                    <span class="wm-pill-label">{{ group.label }}</span>
                    <span class="wm-pill-count">{{ group.items.length }}</span>
                  </div>
                }
                @if (wmGrouped().length === 0) {
                  <div class="empty-state-sm">No active signals</div>
                }
              </div>
            </div>
          }

          <!-- ===================== KNOWLEDGE ===================== -->
          @case ('knowledge') {
            <!-- Domain filter pills -->
            <div class="domain-stats">
              @for (ds of domainStats(); track ds.domain) {
                <div
                  class="ds-pill"
                  [class.ds-active]="isDomainActive(ds.domain)"
                  [class.ds-inactive]="!isDomainActive(ds.domain)"
                  [style.border-color]="domainColor(ds.domain)"
                  (click)="toggleDomainFilter(ds.domain)"
                >
                  <span class="ds-dot" [style.background]="domainColor(ds.domain)"></span>
                  <span class="ds-label">{{ ds.domain }}</span>
                  <span class="ds-count">{{ ds.count }}</span>
                </div>
              }
            </div>

            <div class="facts-toolbar">
              <input
                class="search-input"
                type="text"
                placeholder="Search facts..."
                [ngModel]="factSearch()"
                (ngModelChange)="onFactSearchChange($event)"
              />
              <div class="toolbar-right">
                <label class="fluid-toggle">
                  <input type="checkbox" [checked]="fluidOnly()" (change)="toggleFluidOnly()" />
                  <span>Fluid only</span>
                </label>
                <button class="view-toggle-btn" (click)="toggleFactsView()">
                  {{ factsViewMode() === 'tree' ? '⊞ Table' : '⊟ Tree' }}
                </button>
              </div>
            </div>

            @if (filteredFacts(); as f) {
              @if (factsViewMode() === 'tree') {
                <div class="fact-tree-container">
                  <div class="fact-tree">
                    @for (node of buildFactTree(f); track node.path) {
                      <div class="tree-node" [style.padding-left.px]="node.depth * 24 + 8">
                        <div class="tree-node-row" (click)="node.childCount > 0 ? toggleTreeNode(node.path) : null">
                          @if (node.childCount > 0) {
                            <span class="tree-arrow" [class.expanded]="expandedNodes().has(node.path)">▸</span>
                          } @else {
                            <span class="tree-leaf-dot" [style.background]="domainColor(node.path)"></span>
                          }
                          <span class="tree-label" [style.color]="domainColor(node.path)">{{ node.key }}</span>
                          @if (node.childCount > 0) {
                            <span class="tree-count">{{ node.totalFacts }}</span>
                          }
                        </div>
                        @if (node.facts.length > 0) {
                          @for (fact of node.facts; track fact.id) {
                            <div class="tree-fact" [class.selected]="selectedFact() === fact" (click)="selectFact(fact)">
                              <div class="tree-fact-top">
                                <span class="tree-fact-value">{{ fact.current_value }}</span>
                                <div class="strength-bar-wrap" [title]="'Strength: ' + fact.flip_count + '/' + flipThreshold()">
                                  <div class="strength-bar">
                                    <div class="strength-fill" [style.width.%]="strengthPercent(fact)"></div>
                                  </div>
                                </div>
                              </div>
                              <div class="tree-fact-meta">
                                <button class="fluid-toggle-btn" [class.is-fluid]="fact.is_fluid" (click)="toggleFactFluid(fact, $event)" [title]="fact.is_fluid ? 'Click to mark as static' : 'Click to mark as fluid'">
                                  {{ fact.is_fluid ? 'fluid' : 'static' }}
                                </button>
                                @if (fact.flip_count > 0) {
                                  <span class="flip-badge">{{ fact.flip_count }} flips</span>
                                }
                                <span class="tree-fact-ts">{{ fact.last_modified | timeAgo }}</span>
                              </div>
                              @if (selectedFact() === fact) {
                                <div class="tree-fact-detail">
                                  <div class="detail-row"><span class="detail-label">Domain</span><code>{{ fact.domain_path }}</code></div>
                                  <div class="detail-row"><span class="detail-label">Question</span><span>{{ fact.canonical_question || '—' }}</span></div>
                                  <div class="detail-row"><span class="detail-label">Block</span><span>{{ fact.block_height }}</span></div>
                                  <div class="detail-row"><span class="detail-label">Meta Layer</span><span>{{ fact.meta_layer || 'base' }}</span></div>
                                  <div class="detail-row"><span class="detail-label">Created</span><span>{{ fact.created_at | timeAgo }}</span></div>
                                  @if (parseHormonalFingerprint(fact.hormonal_fingerprint); as fp) {
                                    @if (hasKeys(fp)) {
                                      <div class="detail-row"><span class="detail-label">Hormonal State</span></div>
                                      <div class="hormone-mini-bars">
                                        @for (h of fp | keyvalue; track h.key) {
                                          <div class="hormone-mini">
                                            <span class="hm-name">{{ h.key | slice:0:4 }}</span>
                                            <div class="hm-bar-bg">
                                              <div class="hm-bar-fill" [style.width.%]="(+h.value) * 100" [style.background]="hormoneColor(h.key)"></div>
                                            </div>
                                            <span class="hm-val">{{ (+h.value).toFixed(2) }}</span>
                                          </div>
                                        }
                                      </div>
                                    }
                                  }
                                </div>
                              }
                            </div>
                          }
                        }
                      </div>
                    }
                  </div>
                </div>
              } @else {
                <div class="fact-table-wrap">
                  <table class="fact-table">
                    <thead>
                      <tr>
                        <th>Domain Path</th>
                        <th>Value</th>
                        <th>Strength</th>
                        <th>Flips</th>
                        <th>Fluid</th>
                        <th>Meta Layer</th>
                        <th>Block</th>
                        <th>Modified</th>
                      </tr>
                    </thead>
                    <tbody>
                      @for (fact of f; track fact.id) {
                        <tr>
                          <td class="mono">{{ fact.domain_path }}</td>
                          <td [title]="fact.current_value">{{ truncate(fact.current_value, 40) }}</td>
                          <td>
                            <div class="strength-bar-table">
                              <div class="strength-fill" [style.width.%]="strengthPercent(fact)"></div>
                            </div>
                          </td>
                          <td>{{ fact.flip_count }}</td>
                          <td>
                            <button class="fluid-toggle-btn" [class.is-fluid]="fact.is_fluid" (click)="toggleFactFluid(fact, $event)" [title]="fact.is_fluid ? 'Click to mark as static' : 'Click to mark as fluid'">
                              {{ fact.is_fluid ? 'fluid' : 'static' }}
                            </button>
                          </td>
                          <td>{{ fact.meta_layer || '—' }}</td>
                          <td>{{ fact.block_height }}</td>
                          <td>{{ fact.last_modified | timeAgo }}</td>
                        </tr>
                      }
                    </tbody>
                  </table>
                </div>
              }
              <div class="table-footer">{{ filteredFacts().length }} of {{ facts()?.total ?? 0 }} facts</div>
            } @else {
              <div class="empty-state">Loading facts...</div>
            }
          }

          <!-- ===================== CHAIN ===================== -->
          @case ('chain') {
            @if (chainState(); as cs) {
              <div class="chain-meta">
                <span><strong>Height:</strong> {{ chainDisplayHeight(cs) }}</span>
                <span><strong>Base Model:</strong> {{ cs.base_model_label || cs.base_model }}</span>
                <span><strong>Sovereignty:</strong> {{ cs.sovereignty_mode }}</span>
                <span class="mono"><strong>Soul Hash:</strong> {{ truncate(cs.soul_hash, 16) }}</span>
              </div>

              <div class="chain-timeline">
                @if (genesisBlocks(cs).length) {
                  <div class="tier-label">Genesis</div>
                  @for (block of genesisBlocks(cs); track block.height) {
                    <ng-container *ngTemplateOutlet="blockNode; context: { $implicit: block, active: false }"></ng-container>
                  }
                }
                @if (otherConsolidated(cs).length) {
                  <div class="tier-label">Consolidated</div>
                  @for (block of otherConsolidated(cs); track block.height) {
                    <ng-container *ngTemplateOutlet="blockNode; context: { $implicit: block, active: false }"></ng-container>
                  }
                }

                @if (cs.frozen_epochs.length) {
                  <div class="tier-label">Frozen Epochs</div>
                  @for (block of cs.frozen_epochs; track block.height) {
                    <ng-container *ngTemplateOutlet="blockNode; context: { $implicit: block, active: false }"></ng-container>
                  }
                }

                @if (cs.active_epoch; as ae) {
                  <div class="tier-label active-tier">Active Epoch</div>
                  <ng-container *ngTemplateOutlet="blockNode; context: { $implicit: ae, active: true }"></ng-container>
                }

                @if (cs.active_deltas.length) {
                  <div class="tier-label active-tier">Active Deltas</div>
                  @for (block of cs.active_deltas; track block.height) {
                    <ng-container *ngTemplateOutlet="blockNode; context: { $implicit: block, active: true }"></ng-container>
                  }
                }
              </div>

              <ng-template #blockNode let-block let-active="active">
                <div
                  class="tl-node"
                  [class.active-node]="active"
                  [class.tl-expanded]="selectedForkHeight() === block.height"
                  (click)="selectForkPoint(block.height)"
                >
                  <div class="tl-line"></div>
                  <div class="tl-dot" [class.genesis]="block.block_type === 'genesis'" [class.epoch]="block.block_type === 'epoch'" [class.delta]="block.block_type === 'delta'" [class.active-dot]="active"></div>
                  <div class="tl-content">
                    <div class="tl-row">
                      <span class="tl-height">{{ block.height }}</span>
                      <span class="block-type-badge" [class.genesis]="block.block_type === 'genesis'" [class.epoch]="block.block_type === 'epoch'" [class.delta]="block.block_type === 'delta'">{{ block.block_type }}</span>
                      <span class="tl-aku">{{ block.aku_count }} AKU</span>
                      <span class="tl-hash mono">{{ truncate(block.block_hash, 12) }}</span>
                      <span class="tl-ts">{{ block.timestamp | timeAgo }}</span>
                    </div>
                    @if (selectedForkHeight() === block.height) {
                      <div class="tl-details">
                        <div class="tl-detail-grid">
                          <div class="tl-detail-item">
                            <span class="tl-detail-label">Block Hash</span>
                            <code class="tl-detail-value">{{ block.block_hash }}</code>
                          </div>
                          <div class="tl-detail-item">
                            <span class="tl-detail-label">Parent Hash</span>
                            <code class="tl-detail-value">{{ block.parent_hash }}</code>
                          </div>
                          <div class="tl-detail-item">
                            <span class="tl-detail-label">Delta Path</span>
                            <code class="tl-detail-value">{{ block.delta_path || '—' }}</code>
                          </div>
                          <div class="tl-detail-item">
                            <span class="tl-detail-label">AKU Count</span>
                            <span class="tl-detail-value">{{ block.aku_count }}</span>
                          </div>
                          <div class="tl-detail-item">
                            <span class="tl-detail-label">Timestamp</span>
                            <span class="tl-detail-value">{{ block.timestamp }}</span>
                          </div>
                          @if (block.metadata && hasKeys(block.metadata)) {
                            <div class="tl-detail-item tl-detail-full">
                              <span class="tl-detail-label">Metadata</span>
                              <code class="tl-detail-value tl-detail-json">{{ block.metadata | json }}</code>
                            </div>
                          }
                        </div>
                        <button class="fork-here-btn" (click)="forkFromChain(block.height, $event)">Fork at height {{ block.height }}</button>
                      </div>
                    }
                  </div>
                </div>
              </ng-template>
            } @else {
              <div class="empty-state">Loading chain data...</div>
            }
          }

          <!-- ===================== WORKING MEMORY ===================== -->
          @case ('working-memory') {
            <div class="wm-full">
              <div class="wm-toolbar">
                <h3 class="wm-full-title">
                  Working Memory
                  @if (wmData()?.slot_count; as sc) {
                    <span class="wm-total-badge">{{ sc }}/{{ wmData()?.max_slots }}</span>
                  } @else {
                    <span class="wm-total-badge">{{ contextItems().length }}</span>
                  }
                </h3>
                <div class="wm-ws-switcher">
                  <button class="wm-ws-btn"
                    [class.active]="wmDisplayWorkspace() === 'professional'"
                    (click)="wmViewWorkspace.set('professional')">
                    Professional
                    @if (wmActiveWorkspace() === 'professional') {
                      <span class="wm-ws-active-dot"></span>
                    }
                  </button>
                  <button class="wm-ws-btn"
                    [class.active]="wmDisplayWorkspace() === 'personal'"
                    (click)="wmViewWorkspace.set('personal')">
                    Personal
                    @if (wmActiveWorkspace() === 'personal') {
                      <span class="wm-ws-active-dot"></span>
                    }
                  </button>
                </div>
                <button class="btn-secondary" (click)="loadContext()">Refresh</button>
              </div>

              @if (wmGrouped().length === 0 && contextItems().length === 0) {
                <div class="empty-state">No active signals — start a conversation to build working memory</div>
              }

              <div class="wm-columns">
                @for (group of wmGrouped(); track group.type) {
                  <div class="wm-column">
                    <div class="wm-section-header" [style.border-left-color]="group.color">
                      <span class="wm-section-dot" [style.background]="group.color"></span>
                      <span class="wm-section-label">{{ group.label }}</span>
                      <span class="wm-section-count">{{ group.items.length }}</span>
                    </div>
                    @for (item of group.items; track item.index) {
                      <div class="wm-item">
                        <div class="wm-item-main">
                          <span class="wm-domain">{{ item.domain || 'General' }}</span>
                          <span class="wm-source-pill">{{ item.source }}</span>
                          <span class="wm-ts">{{ relativeTime(item.timestamp) }}</span>
                        </div>
                        @if (wmEditingIndex() === item.index) {
                          <div class="wm-edit-row">
                            <input
                              class="wm-edit-input"
                              [ngModel]="wmEditContent()"
                              (ngModelChange)="wmEditContent.set($event)"
                              (keydown)="onWmEditKeydown($event, item)"
                            />
                            <button class="btn-sm btn-save" (click)="saveWmEdit(item)">Save</button>
                            <button class="btn-sm btn-cancel" (click)="cancelWmEdit()">Cancel</button>
                          </div>
                        } @else {
                          <div class="wm-content-row">
                            <span class="wm-content">{{ item.content }}</span>
                            <div class="wm-actions">
                              <button class="btn-icon" title="Edit" (click)="startWmEdit(item)">✎</button>
                              <button class="btn-icon btn-danger" title="Delete" (click)="deleteWmItem(item)">✕</button>
                            </div>
                          </div>
                        }
                      </div>
                    }
                  </div>
                }
              </div>
            </div>
          }

          <!-- ===================== EPISODES ===================== -->
          @case ('episodes') {
            @if (narrativeData(); as narr) {
              <div class="episodes-header">
                <span class="episodes-stat">Coherence: {{ ((narr.narrative_coherence ?? 0) * 100).toFixed(0) }}%
                  @if (narr.coherence_label) { ({{ narr.coherence_label }}) }
                </span>
                <span class="episodes-stat">{{ narr.episode_count ?? (narr.episodes ?? narr.recent_episodes ?? []).length }} episodes</span>
                @if (narr.active_strategy) {
                  <span class="episodes-stat">Strategy: {{ narr.active_strategy }}</span>
                }
              </div>
              @if (narr.current_episode; as cur) {
                <div class="episode-section-heading">
                  <span class="ep-live-dot"></span> Live Episode
                </div>
                <div class="mem-episode-card current">
                  <div class="mem-ep-header">
                    <span class="mem-ep-number">#{{ cur.index }}</span>
                    <div class="mem-ep-title">{{ cur.title }}</div>
                    <span class="mem-ep-live-badge">LIVE</span>
                  </div>
                  @if (cur.summary) {
                    <div class="mem-ep-summary">{{ cur.summary }}</div>
                  }
                  <div class="mem-ep-detail-row">
                    <span>{{ cur.turns }} turns</span>
                    <span>Arc: {{ cur.arc_summary || cur.arc || 'building...' }}</span>
                    <span>Resonance: {{ (cur.peak_resonance ?? 0).toFixed(2) }}</span>
                    @if (cur.peak_engagement) { <span>Engagement: {{ cur.peak_engagement.toFixed(2) }}</span> }
                  </div>
                  @if (cur.topics?.length) {
                    <div class="mem-ep-topics">
                      @for (t of cur.topics; track t) { <span class="mem-ep-topic-pill">{{ t }}</span> }
                    </div>
                  }
                  @if (cur.domains?.length) {
                    <div class="mem-ep-domains">
                      @for (d of cur.domains; track d) { <span class="mem-ep-domain-pill">{{ d }}</span> }
                    </div>
                  }
                  @if (cur.arc_snapshots?.length) {
                    <div class="mem-ep-arc-visual">
                      <div class="mem-ep-arc-label">Mood Journey</div>
                      <div class="mem-ep-arc-track">
                        @for (snap of cur.arc_snapshots; track snap.turn) {
                          <div class="mem-ep-arc-dot"
                               [title]="'Turn ' + snap.turn + ': ' + snap.mood + ' (v=' + snap.v + ', a=' + snap.a + ')'"
                               [style.background]="moodColor(snap.v)"
                               [style.bottom.%]="(snap.v + 1) * 50">
                          </div>
                        }
                      </div>
                      <div class="mem-ep-arc-moods">
                        @for (snap of cur.arc_snapshots; track snap.turn) {
                          <span class="mem-ep-arc-mood" [style.color]="moodColor(snap.v)">{{ snap.mood }}</span>
                        }
                      </div>
                    </div>
                  }
                </div>
              }
              @if ((narr.episodes ?? narr.recent_episodes)?.length) {
                <div class="episode-section-heading">Episode History</div>
                @for (ep of (narr.episodes ?? narr.recent_episodes)!.slice().reverse(); track ep.index ?? $index) {
                  <div class="mem-episode-card" [class.mem-ep-expanded]="expandedMemEpisode === (ep.index ?? $index)"
                       (click)="expandedMemEpisode = expandedMemEpisode === (ep.index ?? $index) ? null : (ep.index ?? $index)">
                    <div class="mem-ep-header">
                      @if (ep.index) { <span class="mem-ep-number">#{{ ep.index }}</span> }
                      <div class="mem-ep-title">{{ ep.title }}</div>
                      <span class="mem-ep-meta-inline">
                        {{ ep.turns }} turns
                        @if (ep.duration_min) { &bull; {{ ep.duration_min.toFixed(0) }}min }
                      </span>
                      @if (ep.dominant_emotion) {
                        <span class="mem-ep-mood-badge" [style.background]="moodColor(moodValence(ep.dominant_emotion))">{{ ep.dominant_emotion }}</span>
                      }
                      <span class="mem-ep-chevron">{{ expandedMemEpisode === (ep.index ?? $index) ? '▾' : '▸' }}</span>
                    </div>

                    @if (expandedMemEpisode === (ep.index ?? $index)) {
                      <div class="mem-ep-body" (click)="$event.stopPropagation()">
                        @if (ep.summary) {
                          <div class="mem-ep-summary">{{ ep.summary }}</div>
                        }
                        <div class="mem-ep-detail-row">
                          <span>Arc: {{ ep.arc_summary || ep.arc }}</span>
                          <span>{{ ep.opening_mood }} → {{ ep.closing_mood }}</span>
                          <span>Resonance: {{ (ep.peak_resonance ?? 0).toFixed(3) }}</span>
                          @if (ep.peak_engagement) { <span>Engagement: {{ ep.peak_engagement.toFixed(3) }}</span> }
                          @if (ep.peak_cortisol) { <span>Cortisol: {{ ep.peak_cortisol.toFixed(3) }}</span> }
                        </div>
                        @if (ep.coherence_contribution) {
                          <div class="mem-ep-detail-row">
                            <span>Coherence Contribution: {{ (ep.coherence_contribution * 100).toFixed(0) }}%</span>
                          </div>
                        }
                        @if (ep.start_time) {
                          <div class="mem-ep-detail-row">
                            <span>Started: {{ ep.start_time * 1000 | date:'medium' }}</span>
                            @if (ep.end_time) { <span>Ended: {{ ep.end_time * 1000 | date:'medium' }}</span> }
                          </div>
                        }
                        @if (ep.topics?.length) {
                          <div class="mem-ep-topics">
                            @for (t of ep.topics; track t) { <span class="mem-ep-topic-pill">{{ t }}</span> }
                          </div>
                        }
                        @if (ep.domains?.length) {
                          <div class="mem-ep-domains">
                            @for (d of ep.domains; track d) { <span class="mem-ep-domain-pill">{{ d }}</span> }
                          </div>
                        }
                        @if (ep.arc_snapshots?.length) {
                          <div class="mem-ep-arc-visual">
                            <div class="mem-ep-arc-label">Turn-by-Turn Mood</div>
                            <div class="mem-ep-arc-track">
                              @for (snap of ep.arc_snapshots; track snap.turn) {
                                <div class="mem-ep-arc-dot"
                                     [title]="'Turn ' + snap.turn + ': ' + snap.mood + ' (v=' + snap.v + ', a=' + snap.a + ')'"
                                     [style.background]="moodColor(snap.v)"
                                     [style.bottom.%]="(snap.v + 1) * 50">
                                </div>
                              }
                            </div>
                            <div class="mem-ep-arc-moods">
                              @for (snap of ep.arc_snapshots; track snap.turn) {
                                <span class="mem-ep-arc-mood" [style.color]="moodColor(snap.v)">{{ snap.mood }}</span>
                              }
                            </div>
                          </div>
                        }
                      </div>
                    }
                  </div>
                }
              }
              @if (!narr.current_episode && !(narr.episodes ?? narr.recent_episodes)?.length) {
                <div class="empty-state">No episodes recorded yet — episodes are created automatically during conversations</div>
              }
            } @else {
              <div class="empty-state">Loading narrative data...</div>
            }
          }

          <!-- ===================== SOUL ===================== -->
          @case ('soul') {
            <div class="soul-grid">
              <!-- Export -->
              <div class="soul-card">
                <h3 class="soul-card-title">Export Soul Package</h3>
                <p class="soul-desc">Download this agent's full state as a portable .soul.zip file.</p>
                <label class="fluid-toggle soul-toggle">
                  <input type="checkbox" [checked]="exportIncludeSessions()" (change)="exportIncludeSessions.set(!exportIncludeSessions())" />
                  <span>Include conversation history</span>
                </label>
                <button class="btn-primary" [disabled]="soulExporting()" (click)="exportSoul()">
                  {{ soulExporting() ? 'Exporting...' : 'Export Soul Package' }}
                </button>
                @if (soulExportSuccess()) {
                  <div class="soul-success">Soul package downloaded successfully.</div>
                }
              </div>

              <!-- Import -->
              <div class="soul-card">
                <h3 class="soul-card-title">Import Soul Package</h3>
                <p class="soul-desc">Upload a .soul.zip to replace this agent's state.</p>
                <div class="soul-warning">This will overwrite the current agent state. Make an export first if needed.</div>
                <div
                  class="drop-zone"
                  [class.dragover]="importDragover()"
                  (dragover)="onDragOver($event)"
                  (dragleave)="importDragover.set(false)"
                  (drop)="onDrop($event)"
                >
                  <span class="drop-icon">↑</span>
                  <span>Drag .soul.zip here or</span>
                  <label class="drop-browse">
                    browse
                    <input type="file" accept=".zip" hidden (change)="onFileSelect($event)" />
                  </label>
                </div>
                @if (importFile()) {
                  <div class="import-file-info">
                    <span>{{ importFile()!.name }}</span>
                    <button class="btn-primary" [disabled]="soulImporting()" (click)="importSoul()">
                      {{ soulImporting() ? 'Importing...' : 'Import' }}
                    </button>
                  </div>
                }
                @if (importResult()) {
                  <div class="soul-success">Import complete: {{ importResult()!.status }}</div>
                }
              </div>

              <!-- Fork -->
              <div class="soul-card">
                <h3 class="soul-card-title">Fork Agent</h3>
                <p class="soul-desc">Create a new agent from this agent's memory at a specific chain height.</p>
                <div class="fork-form">
                  <div class="fork-fields-row">
                    <div class="fork-field">
                      <label class="fork-label">Fork Height</label>
                      <input
                        class="fork-input"
                        type="number"
                        [ngModel]="forkHeight()"
                        (ngModelChange)="forkHeight.set($event)"
                        placeholder="Chain height"
                        min="0"
                      />
                    </div>
                    <div class="fork-field">
                      <label class="fork-label">New Agent Name</label>
                      <input
                        class="fork-input"
                        type="text"
                        [ngModel]="forkName()"
                        (ngModelChange)="forkName.set($event)"
                        placeholder="Optional name"
                      />
                    </div>
                  </div>
                  <button class="btn-primary" [disabled]="soulForking()" (click)="forkAgent()">
                    {{ soulForking() ? 'Forking...' : 'Fork' }}
                  </button>
                </div>
                @if (forkResult()) {
                  <div class="soul-success">
                    Forked! New agent: <code>{{ forkResult()!.new_agent_id }}</code>
                    ({{ forkResult()!.facts_copied }} facts, height {{ forkResult()!.chain_height }})
                  </div>
                }
              </div>

              <!-- Snapshot -->
              <div class="soul-card">
                <h3 class="soul-card-title">Create Snapshot</h3>
                <p class="soul-desc">Save a restore point of this agent's current state.</p>
                <div class="fork-form">
                  <div class="fork-field">
                    <label class="fork-label">Label (optional)</label>
                    <input
                      type="text"
                      class="fork-input"
                      placeholder="e.g. pre-sleep baseline"
                      [value]="snapshotLabel()"
                      (input)="snapshotLabel.set($any($event.target).value)"
                    />
                  </div>
                  <button class="btn-primary" [disabled]="snapshotCreating()" (click)="createSnapshot()">
                    {{ snapshotCreating() ? 'Creating...' : 'Create Snapshot' }}
                  </button>
                </div>
                @if (snapshotResult()) {
                  <div class="soul-success">
                    Snapshot created at height {{ snapshotResult().chain_height }}
                    — {{ snapshotResult().snapshot_name }}
                  </div>
                }
                @if (snapshots().length) {
                  <div class="snapshot-list">
                    <h4 class="snapshot-list-title">Previous snapshots</h4>
                    @for (snap of snapshots(); track snap.snapshot_name) {
                      <div class="snapshot-item">
                        <div class="snapshot-info">
                          <span class="snapshot-name">{{ snap.label || snap.snapshot_name }}</span>
                          <span class="snapshot-meta">Height {{ snap.chain_height }} · {{ snap.created_at | date:'short' }}</span>
                        </div>
                        <button class="btn-sm btn-outline" (click)="restoreSnapshot(snap)" [disabled]="snapshotRestoring()">
                          {{ snapshotRestoring() && restoringSnapshot() === snap.file ? 'Restoring...' : 'Restore' }}
                        </button>
                      </div>
                    }
                  </div>
                }
              </div>

              <!-- Conversation history (collapsible, full width) -->
              <div class="soul-card soul-card-full">
                <h3 class="soul-card-title soul-collapse-header" (click)="convExpanded.set(!convExpanded())">
                  Conversation History
                  <span class="collapse-arrow" [class.expanded]="convExpanded()">▸</span>
                </h3>
                @if (convExpanded()) {
                  @if (conversation(); as conv) {
                    <div class="message-list">
                      @for (msg of conv.messages; track $index) {
                        <div class="message" [ngClass]="'message-' + msg.role">
                          @if (msg.role !== 'system') {
                            <span class="message-role">{{ msg.role }}</span>
                          }
                          @if (msg.role === 'assistant') {
                            @let parsed = parseMsgTags(msg.content);
                            <div class="message-bubble">
                              {{ parsed.text }}
                              @if (parsed.tags.length) {
                                <div class="conv-signal-tags">
                                  @for (tag of parsed.tags; track tag.raw) {
                                    <span class="conv-signal-pill" [title]="tag.raw">
                                      <span class="conv-signal-dot" [style.background]="msgTagColor(tag.type)"></span>
                                      <span class="conv-signal-type">{{ msgHumanType(tag.type) }}</span>
                                      <span class="conv-signal-label">{{ tag.label }}</span>
                                    </span>
                                  }
                                </div>
                              }
                            </div>
                          } @else {
                            <div class="message-bubble">{{ msg.content }}</div>
                          }
                        </div>
                      }
                      @if (!conv.messages.length) {
                        <div class="empty-state">No conversation history.</div>
                      }
                    </div>
                  } @else {
                    <div class="empty-state">Loading conversation...</div>
                  }
                }
              </div>
            </div>
          }
        }
      </div>
    }

    @if (!loading() && !agent()) {
      <div class="empty-state">Agent not found.</div>
    }
  `,
  styleUrl: './memory.component.scss',
})
export class MemoryComponent implements OnInit {
  agent = signal<Agent | null>(null);
  chainState = signal<ChainState | null>(null);
  facts = signal<FactsResponse | null>(null);
  conversation = signal<{ messages: ConversationMessage[] } | null>(null);

  activeTab = signal<MemTab>('overview');
  loading = signal(true);
  factSearch = signal('');
  fluidOnly = signal(false);

  factsViewMode = signal<'tree' | 'table'>('tree');
  expandedNodes = signal<Set<string>>(new Set());
  selectedFact = signal<Fact | null>(null);

  // Domain filter: empty = all active
  activeDomains = signal<Set<string>>(new Set());

  // Overview expand states
  overviewExpandedFact = signal<number | null>(null);
  overviewExpandedBlock = signal<number | null>(null);

  // Working memory
  contextItems = signal<ContextItem[]>([]);
  wmData = signal<WorkingMemoryStatus | null>(null);
  wmEditingIndex = signal<number | null>(null);
  wmEditContent = signal('');
  /** Which workspace to view: null = active (auto), or explicit override */
  wmViewWorkspace = signal<'professional' | 'personal' | null>(null);

  // Chain fork selection
  selectedForkHeight = signal<number | null>(null);

  // Soul tab state
  exportIncludeSessions = signal(false);
  soulExporting = signal(false);
  soulExportSuccess = signal(false);
  importDragover = signal(false);
  importFile = signal<File | null>(null);
  soulImporting = signal(false);
  importResult = signal<SoulImportResult | null>(null);
  forkHeight = signal<number>(0);
  forkName = signal('');
  soulForking = signal(false);
  forkResult = signal<ForkResult | null>(null);
  snapshotLabel = signal('');
  snapshotCreating = signal(false);
  snapshotResult = signal<any>(null);
  snapshots = signal<any[]>([]);
  snapshotRestoring = signal(false);
  restoringSnapshot = signal('');
  convExpanded = signal(false);

  readonly tabs: { id: MemTab; label: string; icon: string }[] = [
    { id: 'overview', label: 'Overview', icon: '\u25C9' },
    { id: 'knowledge', label: 'Knowledge', icon: '\u25C8' },
    { id: 'chain', label: 'Chain', icon: '\u26D3' },
    { id: 'working-memory', label: 'Working Memory', icon: '\u27E1' },
    { id: 'episodes', label: 'Episodes', icon: '\uD83C\uDFAC' },
    { id: 'soul', label: 'Soul', icon: '\u2726' },
  ];

  narrativeData = signal<any>(null);
  expandedMemEpisode: number | null = null;

  private agentId = '';
  private loadedTabs = new Set<string>();

  constructor(
    private api: ApiService,
    private route: ActivatedRoute,
    private router: Router,
  ) {}

  ngOnInit(): void {
    this.agentId = this.route.snapshot.paramMap.get('agentId') ?? '';
    if (this.agentId) {
      this.loadAgent();
    } else {
      this.loading.set(false);
    }
  }

  loadAgent(): void {
    this.loading.set(true);
    this.api.getAgent(this.agentId).subscribe({
      next: (agent) => {
        this.agent.set(agent);
        this.loading.set(false);
        this.selectTab(this.activeTab());
      },
      error: () => this.loading.set(false),
    });
  }

  selectTab(tab: MemTab): void {
    this.activeTab.set(tab);
    if (!this.loadedTabs.has(tab)) {
      this.loadTabData(tab);
    }
  }

  private loadTabData(tab: MemTab): void {
    if (!this.agentId) return;

    switch (tab) {
      case 'overview':
        this.loadFacts();
        if (!this.loadedTabs.has('chain')) {
          this.api.getAgentChain(this.agentId).subscribe({
            next: (state) => {
              this.chainState.set(state);
              this.loadedTabs.add('chain');
            },
          });
        }
        this.loadContext();
        break;
      case 'knowledge':
        this.loadFacts();
        if (!this.chainState()) {
          this.api.getAgentChain(this.agentId).subscribe({
            next: (state) => {
              this.chainState.set(state);
              this.loadedTabs.add('chain');
            },
          });
        }
        break;
      case 'chain':
        this.api.getAgentChain(this.agentId).subscribe({
          next: (state) => {
            this.chainState.set(state);
            this.loadedTabs.add('chain');
          },
        });
        break;
      case 'working-memory':
        this.loadContext();
        break;
      case 'episodes':
        this.api.getNarrativeEpisodes(this.agentId).subscribe({
          next: (data) => this.narrativeData.set(data),
          error: () => this.narrativeData.set(null),
        });
        break;
      case 'soul':
        this.loadSnapshots();
        if (!this.loadedTabs.has('conversation')) {
          this.api.getAgentConversation(this.agentId).subscribe({
            next: (res) => {
              this.conversation.set(res);
              this.loadedTabs.add('conversation');
            },
          });
        }
        break;
    }
    this.loadedTabs.add(tab);
  }

  /** Ledger height; falls back to max block height when yaml current_height is stale. */
  genesisBlocks(cs: ChainState): Block[] {
    return (cs.consolidated || []).filter(b => b.block_type === 'genesis' || b.height === 0);
  }

  otherConsolidated(cs: ChainState): Block[] {
    return (cs.consolidated || []).filter(b => b.block_type !== 'genesis' && b.height !== 0);
  }

  chainDisplayHeight(cs: ChainState | null): number {
    if (!cs) return 0;
    if (cs.current_height > 0) return cs.current_height;
    const all = [
      ...(cs.consolidated || []),
      ...(cs.frozen_epochs || []),
      ...(cs.active_epoch ? [cs.active_epoch] : []),
      ...(cs.active_deltas || []),
    ];
    if (!all.length) return 0;
    return Math.max(...all.map(b => b.height ?? 0));
  }

  // ── Overview ──────────────────────────────────────────────
  overviewStats = computed(() => {
    const cs = this.chainState();
    const f = this.facts();
    const a = this.agent();
    return {
      factsCount: f?.total ?? 0,
      chainHeight: this.chainDisplayHeight(cs),
      sleepCount: a?.runtime?.sleep_count ?? 0,
      ansSignals: this.contextItems().length,
      sovereignty: cs?.sovereignty_mode ?? '—',
      soulHash: cs?.soul_hash ?? '—',
    };
  });

  recentBlocks = computed((): Block[] => {
    const cs = this.chainState();
    if (!cs) return [];
    const all = [
      ...cs.consolidated,
      ...cs.frozen_epochs,
      ...(cs.active_epoch ? [cs.active_epoch] : []),
      ...cs.active_deltas,
    ];
    return all.slice(-5).reverse();
  });

  recentFacts = computed((): Fact[] => {
    const f = this.facts();
    if (!f) return [];
    return [...f.facts]
      .sort((a, b) => new Date(b.last_modified).getTime() - new Date(a.last_modified).getTime())
      .slice(0, 5);
  });

  toggleOverviewFact(id: number): void {
    this.overviewExpandedFact.set(this.overviewExpandedFact() === id ? null : id);
  }

  toggleOverviewBlock(height: number): void {
    this.overviewExpandedBlock.set(this.overviewExpandedBlock() === height ? null : height);
  }

  wmActiveWorkspace = computed(() => this.wmData()?.active_workspace || 'professional');
  wmDisplayWorkspace = computed(() => this.wmViewWorkspace() ?? this.wmActiveWorkspace());

  // ── Working Memory ──────────────────────────────────────────
  wmGrouped = computed((): ContextGroup[] => {
    const groups: ContextGroup[] = [];
    const wm = this.wmData();
    const viewWs = this.wmDisplayWorkspace();

    if (wm && (wm.goals?.length || wm.slots?.length || wm.instructions?.length)) {
      // Determine which slots/goals to show as primary vs other
      const activeWs = wm.active_workspace || 'professional';
      const showingActive = viewWs === activeWs;
      const primarySlots: WMSlot[] = showingActive
        ? (wm.slots || [])
        : ((wm as any)[`${viewWs}_slots`] ?? []);
      const primaryGoals = showingActive
        ? (wm.goals || [])
        : ((wm as any)[`${viewWs}_goals`] ?? []);
      const otherWs = viewWs === 'professional' ? 'personal' : 'professional';
      const otherSlots: WMSlot[] = showingActive
        ? ((wm as any)[`${otherWs}_slots`] ?? [])
        : (wm.slots || []);

      if (wm.instructions?.length) {
        groups.push({
          type: 'instruction', label: 'Instructions', color: 'var(--accent-primary)',
          items: wm.instructions.map((inst, i) => ({
            index: 40000 + i, signal_type: 'instruction',
            domain: inst.source || 'task', content: inst.content || '',
            source: inst.source || 'task', timestamp: '',
          })),
        });
      }
      if (primaryGoals?.length) {
        groups.push({
          type: 'goal', label: 'Goals', color: 'var(--accent-warn)',
          items: primaryGoals.map((g: any, i: number) => ({
            index: 10000 + i, signal_type: 'goal',
            domain: g.level || 'goal', content: g.content || '',
            source: g.level || 'goal', timestamp: '',
          })),
        });
      }
      const slotTypeDefs: { key: string; label: string; color: string }[] = [
        { key: 'fact', label: 'Active Facts', color: 'var(--accent-success)' },
        { key: 'feeling', label: 'Feelings', color: 'var(--accent-primary)' },
        { key: 'schema', label: 'Schemas', color: '#c084fc' },
        { key: 'user_state', label: 'User State', color: 'var(--accent-primary)' },
        { key: 'prediction', label: 'Predictions', color: 'var(--accent-warn)' },
      ];
      for (const def of slotTypeDefs) {
        const matched = primarySlots.filter(s => s.type === def.key);
        if (matched.length) {
          groups.push({
            type: def.key, label: def.label, color: def.color,
            items: matched.map((s, i) => ({
              index: 20000 + i, signal_type: def.key,
              domain: s.domain || '', content: s.content || '',
              source: def.key, timestamp: '',
            })),
          });
        }
      }
      if (wm.intentions?.length) {
        groups.push({
          type: 'intention', label: 'Intentions', color: '#2dd4bf',
          items: wm.intentions.map((it, i) => ({
            index: 30000 + i, signal_type: 'intention',
            domain: 'prospective', content: it.content || '',
            source: it.trigger || 'trigger', timestamp: '',
          })),
        });
      }
      if (wm.consolidation_context) {
        const chunks = wm.consolidation_context.split('\n').filter(l => l.trim());
        if (chunks.length) {
          groups.push({
            type: 'consolidation', label: 'Session Consolidation', color: 'var(--accent-primary)',
            items: chunks.map((c, i) => ({
              index: 50000 + i, signal_type: 'consolidation',
              domain: 'session', content: c.trim(),
              source: 'consolidation', timestamp: '',
            })),
          });
        }
      }
      if (otherSlots.length) {
        const otherLabel = otherWs.charAt(0).toUpperCase() + otherWs.slice(1);
        groups.push({
          type: `other-${otherWs}`, label: `${otherLabel} WM`, color: '#64748b',
          items: otherSlots.map((s, i) => ({
            index: 60000 + i, signal_type: s.type || 'fact',
            domain: s.domain || '', content: s.content || '',
            source: otherWs, timestamp: '',
          })),
        });
      }
    }

    // Always include ANS context (LEARN/BOND/EVALUATE)
    const ansDefs: { key: string; label: string; color: string; match: (t: string) => boolean }[] = [
      { key: 'LEARN', label: 'Learn', color: 'var(--accent-success)', match: t => t === 'LEARN' },
      { key: 'BOND', label: 'Bond', color: 'var(--accent-primary)', match: t => t === 'BOND' || t === 'BONDING' },
      { key: 'EVALUATE', label: 'Eval', color: 'var(--accent-warn)', match: t => t === 'EVALUATE' },
    ];
    const items = this.contextItems();
    for (const def of ansDefs) {
      const matched = items.filter(c => def.match(c.signal_type));
      if (matched.length > 0) {
        groups.push({ type: def.key, label: def.label, color: def.color, items: matched });
      }
    }
    return groups;
  });

  loadContext(): void {
    if (!this.agentId) return;
    this.api.getAnsContext(this.agentId).subscribe({
      next: (res) => {
        this.contextItems.set(res.items.map((item: any) => ({
          index: item.index,
          signal_type: item.signal_type,
          domain: item.domain,
          content: item.content,
          source: item.source,
          timestamp: item.timestamp,
        })));
      },
    });
    this.api.getWorkingMemory(this.agentId).subscribe({
      next: (d) => this.wmData.set(d),
      error: () => this.wmData.set(null),
    });
  }

  startWmEdit(item: ContextItem): void {
    this.wmEditingIndex.set(item.index);
    this.wmEditContent.set(item.content);
  }

  cancelWmEdit(): void {
    this.wmEditingIndex.set(null);
    this.wmEditContent.set('');
  }

  saveWmEdit(item: ContextItem): void {
    const content = this.wmEditContent().trim();
    if (!content) { this.cancelWmEdit(); return; }
    this.api.updateAnsContextItem(this.agentId, item.index, content).subscribe({
      next: () => {
        this.contextItems.update(items =>
          items.map(i => i.index === item.index ? { ...i, content } : i)
        );
        this.cancelWmEdit();
      },
      error: () => this.cancelWmEdit(),
    });
  }

  onWmEditKeydown(event: KeyboardEvent, item: ContextItem): void {
    if (event.key === 'Enter') { event.preventDefault(); this.saveWmEdit(item); }
    else if (event.key === 'Escape') { this.cancelWmEdit(); }
  }

  deleteWmItem(item: ContextItem): void {
    this.api.deleteAnsContextItem(this.agentId, item.index).subscribe({
      next: () => {
        this.contextItems.update(items => items.filter(i => i.index !== item.index));
      },
    });
  }

  relativeTime(isoTimestamp: string): string {
    if (!isoTimestamp) return '';
    const diff = Date.now() - new Date(isoTimestamp).getTime();
    const seconds = Math.floor(diff / 1000);
    if (seconds < 60) return 'just now';
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes}m ago`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours}h ago`;
    return `${Math.floor(hours / 24)}d ago`;
  }

  // ── Knowledge ──────────────────────────────────────────────
  domainStats = computed(() => {
    const f = this.facts();
    if (!f) return [];
    const counts: Record<string, number> = {};
    for (const fact of f.facts) {
      const top = (fact.domain_path || '').split('.')[0] || 'Other';
      counts[top] = (counts[top] || 0) + 1;
    }
    return Object.entries(counts)
      .map(([domain, count]) => ({ domain, count }))
      .sort((a, b) => b.count - a.count);
  });

  isDomainActive(domain: string): boolean {
    const active = this.activeDomains();
    return active.size === 0 || active.has(domain);
  }

  toggleDomainFilter(domain: string): void {
    const current = new Set(this.activeDomains());
    const stats = this.domainStats();
    const allDomains = stats.map(s => s.domain);

    if (current.size === 0) {
      // All were active; clicking one means "only show this one"
      current.clear();
      current.add(domain);
    } else if (current.has(domain)) {
      current.delete(domain);
      // If removing leaves none selected, reset to all
      if (current.size === 0) {
        // All domains active again (empty set = all)
      }
    } else {
      current.add(domain);
      // If all domains now selected, reset to empty (= all)
      if (current.size >= allDomains.length) {
        current.clear();
      }
    }
    this.activeDomains.set(current);
  }

  filteredFacts = computed((): Fact[] => {
    const f = this.facts();
    if (!f) return [];
    const active = this.activeDomains();
    let result = f.facts;
    if (active.size > 0) {
      result = result.filter(fact => {
        const top = (fact.domain_path || '').split('.')[0] || 'Other';
        return active.has(top);
      });
    }
    if (this.fluidOnly()) {
      result = result.filter(fact => fact.is_fluid);
    }
    return result;
  });

  flipThreshold(): number {
    return this.chainState()?.flip_threshold ?? 5;
  }

  strengthPercent(fact: Fact): number {
    const threshold = this.flipThreshold();
    if (threshold <= 0) return 100;
    return Math.min(100, (fact.flip_count / threshold) * 100);
  }

  loadFacts(): void {
    if (!this.agentId) return;
    this.api.getAgentFacts(this.agentId, {
      search: this.factSearch() || undefined,
    }).subscribe({
      next: (res) => {
        this.facts.set(res);
        this.loadedTabs.add('facts');
      },
    });
  }

  onFactSearchChange(value: string): void {
    this.factSearch.set(value);
    this.loadedTabs.delete('facts');
    this.loadedTabs.delete('knowledge');
    this.loadFacts();
  }

  toggleFluidOnly(): void {
    this.fluidOnly.update(v => !v);
  }

  toggleFactFluid(fact: any, event: Event): void {
    event.stopPropagation();
    const newFluid = !fact.is_fluid;
    const agentId = this.agent()?.runtimeAgentId;
    if (!agentId || !fact.id) return;

    this.api.toggleFactFluid(agentId, fact.id, newFluid).subscribe({
      next: () => {
        const resp = this.facts();
        if (!resp) return;
        const idx = resp.facts.findIndex((f: any) => f.id === fact.id);
        if (idx >= 0) {
          const copy = [...resp.facts];
          copy[idx] = { ...copy[idx], is_fluid: newFluid };
          this.facts.set({ ...resp, facts: copy });
        }
      },
      error: (err) => console.error('Failed to toggle fluid:', err),
    });
  }

  toggleFactsView(): void {
    this.factsViewMode.set(this.factsViewMode() === 'tree' ? 'table' : 'tree');
  }

  buildFactTree(facts: Fact[]): TreeNode[] {
    const root: Record<string, any> = {};
    for (const fact of facts) {
      const parts = (fact.domain_path || '').split('.');
      let node = root;
      for (let i = 0; i < parts.length; i++) {
        const part = parts[i];
        if (!node[part]) {
          node[part] = { __children: {}, __facts: [] };
        }
        if (i === parts.length - 1) {
          node[part].__facts.push(fact);
        }
        node = node[part].__children;
      }
    }
    return this.treeToArray(root, '', 0);
  }

  private treeToArray(node: Record<string, any>, prefix: string, depth: number): TreeNode[] {
    const result: TreeNode[] = [];
    for (const [key, val] of Object.entries(node)) {
      const path = prefix ? `${prefix}.${key}` : key;
      const childKeys = Object.keys(val.__children || {});
      const facts = val.__facts || [];
      result.push({
        key,
        path,
        depth,
        childCount: childKeys.length,
        factCount: facts.length,
        facts,
        totalFacts: facts.length + this.countDescendantFacts(val.__children),
      });
      if (this.expandedNodes().has(path)) {
        result.push(...this.treeToArray(val.__children, path, depth + 1));
      }
    }
    return result;
  }

  private countDescendantFacts(children: Record<string, any>): number {
    let count = 0;
    for (const val of Object.values(children)) {
      count += (val.__facts || []).length;
      count += this.countDescendantFacts(val.__children || {});
    }
    return count;
  }

  toggleTreeNode(path: string): void {
    const expanded = new Set(this.expandedNodes());
    if (expanded.has(path)) expanded.delete(path);
    else expanded.add(path);
    this.expandedNodes.set(expanded);
  }

  selectFact(fact: Fact): void {
    this.selectedFact.set(this.selectedFact() === fact ? null : fact);
  }

  // ── Chain ──────────────────────────────────────────────────
  selectForkPoint(height: number): void {
    this.selectedForkHeight.set(this.selectedForkHeight() === height ? null : height);
  }

  forkFromChain(height: number, event: Event): void {
    event.stopPropagation();
    this.forkHeight.set(height);
    this.selectTab('soul');
  }

  // ── Soul ──────────────────────────────────────────────────
  exportSoul(): void {
    this.soulExporting.set(true);
    this.soulExportSuccess.set(false);
    this.api.exportSoulPackage(this.agentId, this.exportIncludeSessions()).subscribe({
      next: (blob) => {
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `${this.agentId}.soul.zip`;
        a.click();
        URL.revokeObjectURL(url);
        this.soulExporting.set(false);
        this.soulExportSuccess.set(true);
      },
      error: () => this.soulExporting.set(false),
    });
  }

  onDragOver(event: DragEvent): void {
    event.preventDefault();
    this.importDragover.set(true);
  }

  onDrop(event: DragEvent): void {
    event.preventDefault();
    this.importDragover.set(false);
    const file = event.dataTransfer?.files?.[0];
    if (file) this.importFile.set(file);
  }

  onFileSelect(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (file) this.importFile.set(file);
  }

  importSoul(): void {
    const file = this.importFile();
    if (!file) return;
    this.soulImporting.set(true);
    this.importResult.set(null);
    this.api.importSoulPackage(this.agentId, file).subscribe({
      next: (result) => {
        this.importResult.set(result);
        this.soulImporting.set(false);
      },
      error: () => this.soulImporting.set(false),
    });
  }

  forkAgent(): void {
    this.soulForking.set(true);
    this.forkResult.set(null);
    this.api.forkAgent(this.agentId, this.forkHeight(), this.forkName() || undefined).subscribe({
      next: (result) => {
        this.forkResult.set(result);
        this.soulForking.set(false);
      },
      error: () => this.soulForking.set(false),
    });
  }

  createSnapshot(): void {
    this.snapshotCreating.set(true);
    this.snapshotResult.set(null);
    this.api.createSnapshot(this.agentId, this.snapshotLabel() || undefined).subscribe({
      next: (result) => {
        this.snapshotResult.set(result);
        this.snapshotCreating.set(false);
        this.loadSnapshots();
      },
      error: () => this.snapshotCreating.set(false),
    });
  }

  loadSnapshots(): void {
    this.api.listSnapshots(this.agentId).subscribe({
      next: (res) => this.snapshots.set(res.snapshots || []),
      error: () => this.snapshots.set([]),
    });
  }

  restoreSnapshot(snap: any): void {
    if (!confirm(`Restore snapshot "${snap.label || snap.snapshot_name}"? This will overwrite the current agent state.`)) return;
    this.snapshotRestoring.set(true);
    this.restoringSnapshot.set(snap.file);
    this.api.restoreSnapshot(this.agentId, snap.file).subscribe({
      next: () => {
        this.snapshotRestoring.set(false);
        this.restoringSnapshot.set('');
        this.loadFacts();
        this.api.getAgentChain(this.agentId).subscribe({
          next: (state) => this.chainState.set(state),
        });
      },
      error: () => {
        this.snapshotRestoring.set(false);
        this.restoringSnapshot.set('');
      },
    });
  }

  // ── Shared helpers ──────────────────────────────────────────
  truncate(value: string | undefined | null, len = 12): string {
    if (!value) return '—';
    return value.length > len ? value.slice(0, len) + '…' : value;
  }

  lastSegment(path: string): string {
    const parts = (path || '').split('.');
    return parts[parts.length - 1] || path;
  }

  domainColor(path: string): string {
    const top = (path || '').split('.')[0];
    const colors: Record<string, string> = {
      User: 'var(--accent-primary)', Agent: 'var(--accent-success)', World: 'var(--accent-warn)', System: 'var(--accent-primary)',
      Project: 'var(--accent-primary)', General: 'var(--text-muted)', Feedback: 'var(--accent-warn)',
    };
    return colors[top] || 'var(--text-muted)';
  }

  parseHormonalFingerprint(fp: any): Record<string, number> {
    if (!fp || typeof fp !== 'object') {
      if (typeof fp === 'string') {
        try { return JSON.parse(fp); } catch { return {}; }
      }
      return {};
    }
    const result: Record<string, number> = {};
    for (const [k, v] of Object.entries(fp)) {
      if (typeof v === 'number') result[k] = v;
    }
    return result;
  }

  hasKeys(obj: Record<string, any>): boolean {
    return Object.keys(obj).length > 0;
  }

  hormoneColor(name: string): string {
    const map: Record<string, string> = {
      dopamine: 'var(--accent-success)', serotonin: 'var(--accent-primary)', norepinephrine: 'var(--accent-warn)',
      cortisol: 'var(--accent-danger)', oxytocin: 'var(--accent-primary)', acetylcholine: 'var(--accent-primary)',
    };
    return map[name] ?? 'var(--text-muted)';
  }

  parseMsgTags(content: string): { text: string; tags: SignalTag[] } {
    return _parseTags(content);
  }

  msgTagColor(type: string): string {
    return _tagColor(type);
  }

  msgHumanType(type: string): string {
    return _humanType(type);
  }

  moodColor(valence: number): string {
    if (valence >= 0.3) return 'var(--accent-success)';
    if (valence >= 0.1) return 'var(--accent-success)';
    if (valence >= -0.1) return '#94a3b8';
    if (valence >= -0.3) return 'var(--accent-warn)';
    return 'var(--accent-danger)';
  }

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
}
