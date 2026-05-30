import { Component, OnInit, OnDestroy, signal, computed, ChangeDetectorRef, ViewChild } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { ActivatedRoute, Router, NavigationEnd, RouterLink } from '@angular/router';
import { filter } from 'rxjs';
import { HttpClient } from '@angular/common/http';
import { ApiService } from '../../core/services/api.service';
import { ToastService } from '../../shared/toast/toast.service';
import { Agent } from '../../core/models/agent.model';
import { IntegrationCardComponent, IntegrationChannelType } from './integration-card/integration-card.component';
import { SkillCardComponent } from './skill-card/skill-card.component';
import { ToolCardComponent, AgentTool } from './tool-card/tool-card.component';
import { DetailModalComponent } from './detail-modal/detail-modal.component';
import { SchemaConfigFormComponent, ConfigFieldSchema } from './schema-config-form/schema-config-form.component';
import { GoogleConnectModalComponent } from '../../shared/google-connect-modal/google-connect-modal.component';
import { PlatformIntegrationsService } from '../../core/services/platform-integrations.service';
import { PlatformService } from '../../core/services/platform.service';
import {
  buildIntegrationContext,
  emailIsReady,
  googleUsesByo,
  localNestWebhookWarning,
  selfHostedPrerequisiteSteps,
  type IntegrationChannelId,
  usesBaboCloudBackend,
} from '../../core/services/platform-integrations.util';

class ResultCache<T> {
  private store = new Map<string, { data: T; ts: number }>();
  constructor(private ttlMs: number = 5 * 60 * 1000) {}

  get(key: string): T | null {
    const entry = this.store.get(key);
    if (!entry) return null;
    if (Date.now() - entry.ts > this.ttlMs) return null;
    return entry.data;
  }

  getStale(key: string): T | null {
    return this.store.get(key)?.data ?? null;
  }

  set(key: string, data: T): void {
    this.store.set(key, { data, ts: Date.now() });
  }
}

const skillsCache = new ResultCache<any[]>(5 * 60 * 1000);
const extensionsCache = new ResultCache<any[]>(5 * 60 * 1000);

const CHANNEL_SKILL_NAMES: Record<string, IntegrationChannelType> = {
  'email-channel': 'email',
  'telegram-channel': 'telegram',
  'whatsapp-channel': 'whatsapp',
  'google-workspace': 'google-workspace',
};

function isIntegrationSkill(skill: { name: string; config_schema?: { category?: string }[] }): boolean {
  if (!skill.name) return false;
  return skill.name in CHANNEL_SKILL_NAMES;
}

function decodeJwtEmail(): string {
  try {
    const token = localStorage.getItem('access_token');
    if (!token) return '';
    const parts = token.split('.');
    if (parts.length < 2) return '';
    const payload = JSON.parse(atob(parts[1]));
    return payload.email || '';
  } catch {
    return '';
  }
}

@Component({
  selector: 'app-tools',
  standalone: true,
  imports: [
    CommonModule, FormsModule, RouterLink,
    IntegrationCardComponent, SkillCardComponent, ToolCardComponent,
    DetailModalComponent, SchemaConfigFormComponent, GoogleConnectModalComponent,
  ],
  template: `
    @if (loading()) {
      <div class="loading-state">
        <div class="spinner"></div>
        <span>Loading...</span>
      </div>
    }

    @if (!loading()) {
      <header class="shop-header">
        <div class="header-text">
          <h1 class="title">Skills &amp; Tools</h1>
          <p class="subtitle">Agent integrations, skills, and tools</p>
        </div>
      </header>

      <!-- Pending Reviews -->
      @if (pendingReviews().length) {
        <section class="reviews-section">
          <h2 class="section-title">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>
            </svg>
            Pending Reviews
          </h2>
          <div class="review-list">
            @for (r of pendingReviews(); track r.id) {
              <div class="review-card">
                <div class="review-info">
                  <span class="review-label">New skill{{ r.skills?.length > 1 ? 's' : '' }}:</span>
                  @for (sk of r.skills || []; track sk.name) {
                    <span class="review-skill-badge">{{ sk.name }} v{{ sk.version || '?' }}</span>
                  }
                  @if (r.reason) { <span class="review-reason">{{ r.reason }}</span> }
                </div>
                <div class="review-actions">
                  <button class="review-btn approve" (click)="approveReview(r.id)" [disabled]="reviewLoading[r.id]">
                    @if (reviewLoading[r.id] === 'approve') { <span class="btn-spinner"></span> }
                    Approve &amp; Restart
                  </button>
                  <button class="review-btn reject" (click)="rejectReview(r.id)" [disabled]="reviewLoading[r.id]">
                    @if (reviewLoading[r.id] === 'reject') { <span class="btn-spinner"></span> }
                    Reject
                  </button>
                </div>
              </div>
            }
          </div>
        </section>
      }

      <!-- Integrations Grid -->
      <section class="section">
        <h2 class="section-title">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/>
          </svg>
          Integrations
        </h2>
        <p class="section-subtitle">Channel connections for your agent</p>

        @if (agentId) {
          <div
            class="relay-status-banner"
            [class.online]="relayOnline() === true"
            [class.offline]="relayOnline() === false"
            [class.checking]="relayOnline() === null">
            <span class="relay-dot"></span>
            <div class="relay-text">
              @if (relayOnline() === null) {
                <strong>Checking desktop relay…</strong>
                <span>Connecting to your NestJS server to see if Babo Desktop is online.</span>
              } @else if (relayOnline()) {
                <strong>Desktop relay online</strong>
                <span>Telegram and email webhooks can reach this agent. Babo Desktop is connected to {{ platformIntegrations.nestjsUrl() }}.</span>
              } @else {
                <strong>Desktop relay offline</strong>
                <span>This is separate from Babo Cloud being up — your desktop must keep an open WebSocket to {{ platformIntegrations.nestjsUrl() }} for this agent. Restart Babo Desktop if you closed it.</span>
              }
            </div>
          </div>
        }

        @if (localWebhookWarning(); as localWarn) {
          <div class="localhost-webhook-banner">
            <strong>Localhost NestJS</strong>
            <p>{{ localWarn }}</p>
          </div>
        }

        @if (!usesBaboCloudBackend(platformIntegrations.backendChoice())) {
          <div class="integration-prereq-banner">
            <strong>Self-hosted backend</strong>
            <p>Telegram and email need your NestJS server reachable over HTTPS and Babo Desktop online (relay). Configure platform credentials in <a routerLink="/settings" [queryParams]="{ section: 'integrations' }">Settings → Integrations</a>.</p>
            <ul>
              @for (step of selfHostedPrereqSteps(); track step) {
                <li>{{ step }}</li>
              }
            </ul>
          </div>
        }

        <div class="card-grid">
          @for (int of integrations(); track int.name) {
            <app-integration-card
              [skillName]="int.name"
              [channel]="getChannelType(int.name)"
              [status]="getIntegrationStatus(int.name)"
              [connecting]="isConnecting(int.name)"
              (openDetail)="openIntegrationModal(int.name)"
              (quickConnect)="quickConnect(int.name)" />
          }
        </div>
      </section>

      <!-- Skills Grid -->
      <section class="section">
        <h2 class="section-title">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/>
          </svg>
          Skills
        </h2>
        <p class="section-subtitle">Installed skill packages</p>
        @if (nonIntegrationSkills().length === 0) {
          <div class="empty-state">
            <p>No skills installed yet.</p>
            <p class="empty-hint">Ask the agent to build one, or create a skill directory manually.</p>
          </div>
        } @else {
          <div class="card-grid">
            @for (s of nonIntegrationSkills(); track s.name) {
              <app-skill-card
                [skill]="{ name: s.name, version: s.version, description: s.description, status: s.status, enabled_for_agent: s.enabled_for_agent, created_by: s.created_by, dependencies: s.dependencies, error: s.error, skill_type: s.skill_type, source: s.source, myelination_score: s.myelination_score, crystallization_ready: s.crystallization_ready, crystallized_from: s.crystallized_from }"
                (openDetail)="openSkillModal(s.name)"
                (toggleEnabled)="toggleSkillForAgent(s.name, $event)" />
            }
          </div>
        }
      </section>

      <!-- Connected Extensions -->
      @if (connectedExtensions().length) {
        <section class="section">
          <h2 class="section-title">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M12 22c5.523 0 10-4.477 10-10S17.523 2 12 2 2 6.477 2 12s4.477 10 10 10z"/><path d="M8 12l2 2 4-4"/>
            </svg>
            Connected Extensions
          </h2>
          <p class="section-subtitle">Active MCP server connections</p>
          <div class="card-grid">
            @for (ext of connectedExtensions(); track ext.name) {
              <div class="extension-status-card">
                <div class="ext-status-info">
                  <span class="ext-status-dot connected"></span>
                  <span class="ext-status-name">{{ ext.name }}</span>
                  <span class="ext-status-tools">{{ ext.tools }} tools</span>
                </div>
                <button class="ext-disconnect-btn" (click)="disconnectExtension(ext.name)" [disabled]="extensionDisconnecting() === ext.name">
                  @if (extensionDisconnecting() === ext.name) { <span class="btn-spinner"></span> }
                  @else { Disconnect }
                </button>
              </div>
            }
          </div>
        </section>
      }

      <!-- Community Skills & Extensions -->
      <section class="section clawhub-section">
        <h2 class="section-title">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="12" cy="12" r="10"/><path d="M8 12l2 2 4-4"/>
          </svg>
          Community Skills &amp; Extensions
        </h2>
        <p class="section-subtitle">Browse skills from ClawHub and extensions from the MCP ecosystem</p>

        <div class="clawhub-search">
          <input type="text" placeholder="Search skills &amp; extensions..."
            [value]="clawhubQuery()"
            (input)="onClawhubSearch($any($event.target).value)" />
        </div>

        <div class="source-filter-row">
          <div class="source-filter-tabs">
            <button class="source-tab" [class.active]="sourceFilter() === 'all'" (click)="setSourceFilter('all')">All</button>
            <button class="source-tab" [class.active]="sourceFilter() === 'skills'" (click)="setSourceFilter('skills')">Skills</button>
            <button class="source-tab" [class.active]="sourceFilter() === 'extensions'" (click)="setSourceFilter('extensions')">Extensions</button>
          </div>
          @if (sourceFilter() !== 'extensions') {
            <div class="clawhub-categories">
              @for (cat of clawhubCategories; track cat.label) {
                <button class="category-chip" [class.active]="clawhubActiveCategory() === cat.label"
                  (click)="browseClawhubCategory(cat)">
                  {{ cat.label }}
                </button>
              }
            </div>
          }
        </div>

        @if (clawhubLoading()) {
          <div class="loading-state"><span class="spinner"></span> Searching...</div>
        } @else if (mergedResults().length) {
          <div class="clawhub-results">
            @for (r of mergedResults(); track r._id) {
              <div class="clawhub-result-card" (click)="openCommunityDetail(r)">
                <div class="clawhub-result-info">
                  <div class="clawhub-result-header">
                    <span class="clawhub-result-name">{{ r.displayName || r.name || r.slug }}</span>
                    <span class="source-badge" [class.badge-skill]="r._source === 'skill'" [class.badge-extension]="r._source === 'extension'">
                      {{ r._source === 'extension' ? 'Extension' : 'Skill' }}
                    </span>
                    @if (r.stats?.stars || r.stars) {
                      <span class="clawhub-stars">{{ r.stats?.stars || r.stars }}</span>
                    }
                    @if (r.stats?.downloads) {
                      <span class="clawhub-downloads">{{ formatDownloads(r.stats.downloads) }}</span>
                    }
                  </div>
                  <span class="clawhub-result-desc">{{ r.summary || r.description || '' }}</span>
                </div>
                <div class="clawhub-result-action" (click)="$event.stopPropagation()">
                  @if (r._source === 'extension') {
                    @if (r.installed) {
                      <span class="ext-connected-label">Connected</span>
                    } @else if (r.url || r.command || r.source === 'saved') {
                      <button class="clawhub-install-btn ext-connect-btn" (click)="connectExtension(r)" [disabled]="extensionConnecting() === r.name">
                        @if (extensionConnecting() === r.name) { <span class="btn-spinner"></span> }
                        @else { Connect }
                      </button>
                    } @else if (r.install_command) {
                      <span class="ext-install-hint" [title]="r.install_command">Needs setup</span>
                    } @else if (r.github_url) {
                      <a class="ext-github-link" [href]="r.github_url" target="_blank" rel="noopener">GitHub</a>
                    } @else {
                      <span class="ext-install-hint">Ask agent</span>
                    }
                  } @else {
                    <button class="clawhub-install-btn" (click)="installClawhubSkill(r.slug)" [disabled]="clawhubInstalling() === r.slug">
                      @if (clawhubInstalling() === r.slug) { <span class="btn-spinner"></span> }
                      @else { Install }
                    </button>
                  }
                </div>
              </div>
            }
          </div>
        } @else if (clawhubError()) {
          <p class="empty-state clawhub-empty">{{ clawhubError() }}</p>
        } @else if (sourceFilter() === 'extensions' && !clawhubQuery() && !clawhubLoading()) {
          <p class="empty-state clawhub-empty">Search for extensions above, or browse the <a class="inline-link" (click)="setSourceFilter('all')">full catalog</a></p>
        } @else if (clawhubQuery() && !clawhubLoading()) {
          <p class="empty-state clawhub-empty">No results found for "{{ clawhubQuery() }}"</p>
        }
      </section>

      <!-- Community Detail Modal -->
      <app-detail-modal
        [open]="!!communityDetailItem()"
        [title]="communityDetailItem()?.displayName || communityDetailItem()?.name || communityDetailItem()?.slug || ''"
        (closed)="communityDetailItem.set(null)">
        @if (communityDetailItem(); as item) {
          <div class="community-detail">
            <div class="cd-badges">
              <span class="source-badge" [class.badge-skill]="item._source === 'skill'" [class.badge-extension]="item._source === 'extension'">
                {{ item._source === 'extension' ? 'Extension' : 'Skill' }}
              </span>
              @if (item.stats?.stars || item.stars) {
                <span class="cd-stat">{{ item.stats?.stars || item.stars }} stars</span>
              }
              @if (item.stats?.downloads) {
                <span class="cd-stat">{{ formatDownloads(item.stats.downloads) }} downloads</span>
              }
              @if (item.transport_type) {
                <span class="cd-stat">{{ item.transport_type }} transport</span>
              }
            </div>
            <p class="cd-description">{{ item.summary || item.description || 'No description available.' }}</p>

            @if (item._source === 'extension') {
              @if (item.github_url) {
                <div class="cd-field">
                  <span class="cd-label">GitHub</span>
                  <a [href]="item.github_url" target="_blank" rel="noopener" class="cd-link">{{ item.github_url }}</a>
                </div>
              }
              @if (item.install_command) {
                <div class="cd-field">
                  <span class="cd-label">Install command</span>
                  <code class="cd-code">{{ item.install_command }}</code>
                </div>
              }
              @if (item.url) {
                <div class="cd-field">
                  <span class="cd-label">Server URL</span>
                  <code class="cd-code">{{ item.url }}</code>
                </div>
              }
              @if (item.command) {
                <div class="cd-field">
                  <span class="cd-label">Command</span>
                  <code class="cd-code">{{ item.command }} {{ (item.args || []).join(' ') }}</code>
                </div>
              }
            } @else {
              @if (item.author) {
                <div class="cd-field">
                  <span class="cd-label">Author</span>
                  <span>{{ item.author }}</span>
                </div>
              }
              @if (item.version) {
                <div class="cd-field">
                  <span class="cd-label">Version</span>
                  <span>{{ item.version }}</span>
                </div>
              }
              @if (item.tags?.length) {
                <div class="cd-tags">
                  @for (tag of item.tags; track tag) {
                    <span class="cd-tag">{{ tag }}</span>
                  }
                </div>
              }
            }

            <div class="cd-actions">
              @if (item._source === 'extension') {
                @if (item.installed) {
                  <span class="ext-connected-label">Connected</span>
                } @else if (item.url || item.command || item.source === 'saved') {
                  <button class="cd-primary-btn ext-connect-btn" (click)="connectExtension(item)" [disabled]="extensionConnecting() === item.name">
                    @if (extensionConnecting() === item.name) { <span class="btn-spinner"></span> }
                    @else { Connect Extension }
                  </button>
                } @else {
                  <span class="ext-install-hint">Requires manual setup — ask the agent for help</span>
                }
              } @else {
                <button class="cd-primary-btn" (click)="installClawhubSkill(item.slug); communityDetailItem.set(null)" [disabled]="clawhubInstalling() === item.slug">
                  @if (clawhubInstalling() === item.slug) { <span class="btn-spinner"></span> }
                  @else { Install Skill }
                </button>
              }
            </div>
          </div>
        }
      </app-detail-modal>

      <!-- Agent Tools Grid -->
      <section class="section tools-section">
        <h2 class="section-title">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M14.7 6.3a1 1 0 000 1.4l1.6 1.6a1 1 0 001.4 0l3.77-3.77a6 6 0 01-7.94 7.94l-6.91 6.91a2.12 2.12 0 01-3-3l6.91-6.91a6 6 0 017.94-7.94l-3.76 3.76z"/>
          </svg>
          Agent Tools
        </h2>
        <p class="section-subtitle">{{ agentTools().length }} tools enabled for this agent</p>
        <div class="card-grid tools-grid">
          @for (tool of agentTools(); track tool.name) {
            <app-tool-card [tool]="tool" />
          }
        </div>
      </section>
    }

    <!-- ═══ Integration Detail Modal ═══ -->
    <app-detail-modal
      [open]="!!activeIntegration()"
      [title]="activeIntegrationTitle()"
      (closed)="activeIntegration.set(null)">

      @if (activeIntegration(); as intName) {
        <!-- Not connected: channel-specific setup -->
        @if (!getIntegrationStatus(intName)?.connected) {
          @if (getChannelType(intName) === 'email') {
            <div class="modal-action-block">
              <p class="modal-intro">{{ getSkillOnboarding(intName)?.intro_message || 'Activate your email channel.' }}</p>
              <ol class="modal-steps-list">
                @for (step of getIntegrationContext('email').setupSteps; track step) {
                  <li>{{ step }}</li>
                }
              </ol>
              @if (!emailChannelReady()) {
                <p class="modal-warning">
                  {{ getIntegrationContext('email').blockedReason }}
                  <a routerLink="/settings" [queryParams]="{ section: 'integrations' }">Open Settings → Integrations</a>
                </p>
              }
              <button class="modal-action-btn" (click)="connectEmail(intName)" [disabled]="emailActivating() || !emailChannelReady()">
                @if (emailActivating()) { <span class="btn-spinner"></span> Activating... }
                @else { Activate Email }
              </button>
              <button class="modal-action-btn secondary" type="button" (click)="connectEmailInChat()">Setup in Chat</button>
            </div>
          }
          @if (getChannelType(intName) === 'telegram') {
            <div class="modal-action-block">
              <p class="modal-intro">{{ getSkillOnboarding(intName)?.intro_message || 'Set up Telegram for your agent.' }}</p>
              <ol class="modal-steps-list">
                @for (step of getIntegrationContext('telegram').setupSteps; track step) {
                  <li>{{ step }}</li>
                }
              </ol>
              <button class="modal-action-btn telegram" (click)="connectTelegram()">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg>
                Setup in Chat
              </button>
            </div>
          }
          @if (getChannelType(intName) === 'google-workspace') {
            <div class="modal-action-block">
              <p class="modal-intro">{{ getSkillOnboarding(intName)?.intro_message || 'Connect your Google account to let your agent manage Gmail, Calendar, Drive, and Sheets.' }}</p>
              <ol class="modal-steps-list">
                @for (step of getIntegrationContext('google-workspace').setupSteps; track step) {
                  <li>{{ step }}</li>
                }
              </ol>
              @if (googleUsesByoCredentials()) {
                <button class="modal-action-btn" (click)="connectGoogleInChat()">
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg>
                  Setup in Chat
                </button>
              }
              <button class="modal-action-btn" (click)="connectGoogleWorkspace()">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg>
                {{ googleUsesByoCredentials() ? 'Connect (after OAuth app saved)' : 'Connect Google Account' }}
              </button>
            </div>
          }
          @if (getChannelType(intName) === 'whatsapp') {
            <div class="modal-action-block">
              <p class="modal-intro">{{ getSkillOnboarding(intName)?.intro_message || 'Pair WhatsApp with your agent.' }}</p>
              <ol class="modal-steps-list compact">
                @for (step of getIntegrationContext('whatsapp').setupSteps; track step) {
                  <li>{{ step }}</li>
                }
              </ol>
              @if (whatsappQR()) {
                <div class="qr-block">
                  <img [src]="whatsappQR()" alt="WhatsApp QR Code" class="qr-img" />
                  <p class="qr-hint">Open WhatsApp &rarr; Linked Devices &rarr; Scan this code</p>
                  <p class="qr-status">{{ whatsappStatus() === 'connecting' ? 'Waiting for scan...' : whatsappStatus() }}</p>
                </div>
              } @else {
                <button class="modal-action-btn whatsapp" (click)="startWhatsAppPairing()" [disabled]="whatsappPairing()">
                  @if (whatsappPairing()) { <span class="btn-spinner"></span> Starting... }
                  @else { Start Pairing }
                </button>
              }
            </div>
          }
        }

        <!-- Connected: setup completion prompt -->
        @if (getIntegrationStatus(intName)?.connected && showCompleteSetupPrompt(intName)) {
          <div class="complete-setup-prompt">
            <strong>Complete setup:</strong> fill in the required fields below, then save.
          </div>
        }

        <!-- Config form -->
        @if (getIntegrationSchema(intName).length > 0) {
          <app-schema-config-form
            [schema]="getIntegrationSchema(intName)"
            [values]="getIntegrationConfigValues(intName)"
            [saving]="integrationConfigSaving(intName)"
            [saveSuccess]="integrationConfigSaveSuccess(intName)"
            [readOnlyKeys]="getReadOnlyKeys(intName)"
            (configChange)="onIntegrationConfigChange(intName, $event)"
            (save)="saveIntegrationConfig(intName, integrationDrafts()[intName] || getIntegrationConfigValues(intName))" />
        }
      }
    </app-detail-modal>

    <!-- ═══ Skill Detail Modal ═══ -->
    <app-detail-modal
      [open]="!!activeSkill()"
      [title]="activeSkill() || ''"
      (closed)="closeSkillModal()">

      @if (activeSkill(); as skName) {
        <!-- Error banner -->
        @if (getSkillError(skName)) {
          <div class="skill-error-banner">
            <div class="error-text">{{ getSkillError(skName) }}</div>
            <div class="error-actions">
              @if (repairRunning() && repairSkillName() === skName) {
                <span class="skill-repair-progress">
                  <span class="btn-spinner"></span>
                  <span>{{ repairStep() || 'Starting repair...' }}</span>
                </span>
              } @else if (repairResult() === 'failed' && repairSkillName() === skName) {
                <button class="skill-repair-btn" (click)="startRepair(skName)">Retry</button>
                <button class="skill-escalate-btn" (click)="escalateToChat(skName, getSkillError(skName) || '')">Escalate to Chat</button>
              } @else {
                <button class="skill-repair-btn" (click)="startRepair(skName)" [disabled]="repairRunning()">Fix with Agent</button>
              }
            </div>
          </div>
        }

        <!-- Tabs -->
        <div class="modal-tabs">
          <button class="modal-tab" [class.active]="skillTab() === 'config'" (click)="skillTab.set('config')">Configuration</button>
          <button class="modal-tab" [class.active]="skillTab() === 'files'" (click)="skillTab.set('files')">Files</button>
          <button class="modal-tab" [class.active]="skillTab() === 'brain'" (click)="openBrainTab(skName)">Brain</button>
        </div>

        @if (skillTab() === 'config') {
          <div class="modal-tab-content">
            @if (skillConfigLoading()) {
              <div class="panel-loading"><span class="btn-spinner"></span> Loading config...</div>
            } @else if (skillConfigSchema().length > 0) {
              <app-schema-config-form
                [schema]="skillConfigSchema()"
                [values]="skillConfigData()"
                [saving]="configSaving()"
                [saveSuccess]="configSaveSuccess()"
                (configChange)="onConfigChange($event.key, $event.value)"
                (save)="saveConfig(skName)" />
            } @else if (skillConfigKeys().length === 0) {
              <p class="no-config">No config.json found for this skill.</p>
            } @else {
              <div class="config-form">
                @for (key of skillConfigKeys(); track key) {
                  <div class="config-field">
                    <label class="config-label">{{ key }}</label>
                    <input type="text" class="config-input"
                      [value]="skillConfigData()[key]"
                      (change)="onConfigChange(key, $any($event.target).value)" />
                  </div>
                }
                <button class="modal-action-btn" (click)="saveConfig(skName)" [disabled]="configSaving()">
                  @if (configSaving()) { <span class="btn-spinner"></span> }
                  {{ configSaveSuccess() ? 'Saved!' : 'Save Configuration' }}
                </button>
              </div>
            }
          </div>
        }

        @if (skillTab() === 'files') {
          <div class="modal-tab-content">
            @if (!skillFiles().length) {
              <p class="no-config">No files found.</p>
            } @else {
              <div class="file-list">
                @for (f of skillFiles(); track f.path) {
                  <div class="file-item" [class.active]="activeFile() === f.path" (click)="openFile(skName, f.path)">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                      <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/>
                    </svg>
                    <span class="file-name">{{ f.name }}</span>
                    <span class="file-size">{{ formatFileSize(f.size) }}</span>
                  </div>
                }
              </div>
              @if (activeFile()) {
                <div class="file-viewer">
                  <div class="file-viewer-header">
                    <span>{{ activeFile() }}</span>
                    @if (fileEditable()) {
                      <button class="file-save-btn" (click)="saveFile(skName)" [disabled]="fileSaving()">
                        @if (fileSaving()) { <span class="btn-spinner"></span> }
                        {{ fileSaveSuccess() ? 'Saved!' : 'Save' }}
                      </button>
                    }
                  </div>
                  @if (fileLoading()) {
                    <div class="panel-loading"><span class="btn-spinner"></span> Loading...</div>
                  } @else {
                    <textarea class="file-content"
                      [value]="fileContent()"
                      [readOnly]="!fileEditable()"
                      (input)="onFileEdit($any($event.target).value)"
                      spellcheck="false"></textarea>
                  }
                </div>
              }
            }
          </div>
        }

        @if (skillTab() === 'brain') {
          <div class="modal-tab-content brain-tab">
            @if (brainLoading()) {
              <div class="panel-loading"><span class="btn-spinner"></span> Loading brain stats...</div>
            } @else {
              <div class="brain-stats">
                <div class="brain-stat">
                  <span class="brain-stat-label">Myelination</span>
                  <div class="brain-progress">
                    <div class="brain-progress-fill" [style.width.%]="(brainData()?.myelination_score || 0) * 100"></div>
                  </div>
                  <span class="brain-stat-value">{{ ((brainData()?.myelination_score || 0) * 100).toFixed(0) }}%</span>
                </div>
                <div class="brain-stat">
                  <span class="brain-stat-label">Total Uses</span>
                  <span class="brain-stat-value">{{ brainData()?.total_uses || 0 }}</span>
                </div>
                <div class="brain-stat">
                  <span class="brain-stat-label">Success Rate</span>
                  <span class="brain-stat-value">{{ ((brainData()?.success_rate || 0) * 100).toFixed(0) }}%</span>
                </div>
                @if (brainData()?.associated_domains?.length) {
                  <div class="brain-stat">
                    <span class="brain-stat-label">Associated Domains</span>
                    <div class="brain-domains">
                      @for (d of brainData()?.associated_domains || []; track d) {
                        <span class="brain-domain-chip">{{ d }}</span>
                      }
                    </div>
                  </div>
                }
                @if (brainData()?.readiness_score != null) {
                  <div class="brain-stat">
                    <span class="brain-stat-label">Crystallization Readiness</span>
                    <div class="brain-progress">
                      <div class="brain-progress-fill crystal" [style.width.%]="(brainData()?.readiness_score || 0) * 100"></div>
                    </div>
                    <span class="brain-stat-value">{{ ((brainData()?.readiness_score || 0) * 100).toFixed(0) }}%</span>
                  </div>
                }
              </div>
            }
          </div>
        }

        <!-- Actions -->
        <div class="modal-actions">
          @if (getSkillStatus(skName) === 'loaded') {
            <button class="action-btn disable" (click)="disableSkill(skName)">Disable</button>
          } @else if (getSkillStatus(skName) === 'disabled') {
            <button class="action-btn enable" (click)="enableSkill(skName)">Enable</button>
          }
          <button class="action-btn delete" (click)="deleteSkill(skName)">Delete</button>
        </div>
      }
    </app-detail-modal>

    <app-google-connect-modal
      [open]="googleModalOpen()"
      [agentId]="agentId"
      [requiresByo]="googleUsesByoCredentials()"
      (closed)="closeGoogleModal()"
      (connected)="onGoogleConnected($event)" />

  `,
  styleUrl: './tools.component.scss',
})
export class ToolsComponent implements OnInit, OnDestroy {
  agent = signal<Agent | null>(null);
  loading = signal<boolean>(true);
  skills = signal<any[]>([]);
  pendingReviews = signal<any[]>([]);
  reviewLoading: Record<string, string> = {};
  agentTools = signal<AgentTool[]>([]);

  // Modal state
  googleModalOpen = signal(false);
  activeIntegration = signal<string | null>(null);
  activeSkill = signal<string | null>(null);

  skillTab = signal<'config' | 'files' | 'brain'>('config');
  skillConfigData = signal<Record<string, unknown>>({});
  skillConfigSchema = signal<ConfigFieldSchema[]>([]);
  skillConfigLoading = signal(false);
  configSaving = signal(false);
  configSaveSuccess = signal(false);

  skillFiles = signal<{ path: string; name: string; size: number }[]>([]);
  activeFile = signal<string | null>(null);
  fileContent = signal('');
  fileEditable = signal(false);
  fileLoading = signal(false);
  fileSaving = signal(false);
  fileSaveSuccess = signal(false);
  private editedFileContent = '';

  private integrationConfigCache = signal<Record<string, { config: Record<string, unknown>; schema: ConfigFieldSchema[] }>>({});
  private integrationSaveSuccess = signal<Record<string, boolean>>({});
  private integrationSaving = signal<Record<string, boolean>>({});
  integrationDrafts = signal<Record<string, Record<string, unknown>>>({});

  agentId = '';
  private runtimeAgentId = '';
  private userEmail = '';

  integrations = computed(() => this.skills().filter((s) => isIntegrationSkill(s)));
  nonIntegrationSkills = computed(() => this.skills().filter((s) => !isIntegrationSkill(s)));

  activeIntegrationTitle = computed(() => {
    const name = this.activeIntegration();
    if (!name) return '';
    const ch = CHANNEL_SKILL_NAMES[name];
    const titles: Record<string, string> = {
      email: 'Email',
      telegram: 'Telegram',
      whatsapp: 'WhatsApp',
      'google-workspace': 'Google Workspace',
    };
    return titles[ch] ?? name;
  });

  skillConfigKeys = computed(() => Object.keys(this.skillConfigData()));

  repairRunning = signal(false);
  repairSkillName = signal<string>('');
  repairStep = signal<string>('');
  repairResult = signal<'success' | 'failed' | null>(null);

  skillOnboarding = signal<Record<string, any>>({});
  emailAlias = signal('');
  emailActivating = signal(false);

  telegramConnected = signal(false);
  telegramBotUsername = signal('');

  whatsappQR = signal('');
  whatsappPairing = signal(false);
  whatsappConnected = signal(false);
  whatsappPhone = signal('');
  whatsappStatus = signal('');
  private whatsappQrPoller: ReturnType<typeof setInterval> | null = null;

  googleWorkspaceConnected = signal(false);
  googleWorkspaceEmail = signal('');

  // ClawHub + MCP Extensions (unified search)
  clawhubQuery = signal('');
  clawhubResults = signal<any[]>([]);
  mcpResults = signal<any[]>([]);
  clawhubLoading = signal(false);
  clawhubInstalling = signal<string | null>(null);
  clawhubActiveCategory = signal('Popular');
  clawhubError = signal('');
  private clawhubSearchTimer: ReturnType<typeof setTimeout> | null = null;

  connectedExtensions = signal<{ name: string; tools: number; resources: number }[]>([]);
  extensionConnecting = signal<string | null>(null);
  extensionDisconnecting = signal<string | null>(null);

  sourceFilter = signal<'all' | 'skills' | 'extensions'>('all');

  communityDetailItem = signal<any | null>(null);

  mergedResults = computed(() => {
    const filter = this.sourceFilter();
    const skills = filter === 'extensions' ? [] : this.clawhubResults().map((r: any, i: number) => ({
      ...r, _source: 'skill', _id: `skill-${r.slug || i}`,
    }));
    const extensions = filter === 'skills' ? [] : this.mcpResults().map((r: any, i: number) => ({
      ...r, _source: 'extension', _id: `ext-${r.name || i}`,
      slug: r.name,
    }));
    return [...extensions, ...skills];
  });

  selfHostedPrereqSteps = computed(() =>
    selfHostedPrerequisiteSteps(
      this.platformIntegrations.backendChoice(),
      this.platformIntegrations.nestjsUrl(),
      this.platformIntegrations.capabilities(),
    ),
  );

  localWebhookWarning = computed(() =>
    localNestWebhookWarning(
      this.platformIntegrations.backendChoice(),
      this.platformIntegrations.nestjsUrl(),
    ),
  );

  emailChannelReady = computed(() =>
    emailIsReady(
      this.platformIntegrations.capabilities(),
      this.platformIntegrations.backendChoice(),
    ),
  );

  clawhubCategories = [
    { label: 'Popular', sort: 'downloads' },
    { label: 'Highlighted', sort: 'highlighted' },
    { label: 'Newest', sort: 'newest' },
    { label: 'Top Rated', sort: 'stars' },
  ];

  // Brain tab
  brainData = signal<any>(null);
  brainLoading = signal(false);

  /** NestJS relay WS: desktop runtime reachable for webhook channels. */
  relayOnline = signal<boolean | null>(null);
  private relayPollTimer: ReturnType<typeof setInterval> | null = null;

  constructor(
    private api: ApiService,
    private route: ActivatedRoute,
    private router: Router,
    private cdr: ChangeDetectorRef,
    private http: HttpClient,
    private toast: ToastService,
    readonly platformIntegrations: PlatformIntegrationsService,
    private platform: PlatformService,
  ) {}

  private routerSub?: ReturnType<typeof this.router.events.subscribe>;

  ngOnInit(): void {
    this.agentId = this.route.snapshot.paramMap.get('agentId') ?? '';
    this.userEmail = decodeJwtEmail();
    void this.platformIntegrations.refresh();
    this.loadData();
    this.loadReviews();
    this.loadAgentTools();
    this.loadFeaturedSkills('Popular');
    this.loadConnectedExtensions();
    this.routerSub = this.router.events.pipe(
      filter((e): e is NavigationEnd => e instanceof NavigationEnd),
    ).subscribe((e) => {
      if (this.agentId && this.skills().length > 0 && e.urlAfterRedirects?.includes('/tools')) {
        void this.platformIntegrations.refresh();
        void this.refreshRelayStatus();
        this.loadChannelStatuses(this.skills());
      }
    });
  }

  ngOnDestroy(): void {
    this.routerSub?.unsubscribe();
    this.stopWhatsAppQrPoll();
    this.stopRelayPolling();
  }

  private startRelayPolling(): void {
    this.stopRelayPolling();
    if (!this.agentId) return;
    void this.refreshRelayStatus();
    this.relayPollTimer = setInterval(() => void this.refreshRelayStatus(), 10_000);
  }

  private stopRelayPolling(): void {
    if (this.relayPollTimer) {
      clearInterval(this.relayPollTimer);
      this.relayPollTimer = null;
    }
  }

  private refreshRelayStatus(): void {
    if (!this.agentId) return;
    const runtimeId = this.runtimeAgentId || this.agentId;

    if (this.platform.isElectron) {
      this.api.getLocalRelayStatus(runtimeId).subscribe({
        next: (res) => this.relayOnline.set(!!res.online),
        error: () => this.relayOnline.set(false),
      });
      return;
    }

    this.api.getRelayStatus(this.agentId).subscribe({
      next: (res) => this.relayOnline.set(res.online),
      error: () => this.relayOnline.set(false),
    });
  }

  // ── Data loading ──────────────────────────────────────────

  loadData(): void {
    this.loading.set(true);
    if (!this.agentId) {
      this.api.getSkills().subscribe({
        next: (s) => {
          this.skills.set(s ?? []);
          this.loadIntegrationConfigs();
          this.loading.set(false);
        },
        error: () => { this.skills.set([]); this.loading.set(false); },
      });
      return;
    }
    this.api.getAgent(this.agentId).subscribe({
      next: (agent) => {
        this.agent.set(agent);
        this.runtimeAgentId = agent.runtimeAgentId ?? '';
        this.startRelayPolling();
        this.loadSkills();
        this.loading.set(false);
      },
      error: () => {
        this.api.getSkills().subscribe({
          next: (s) => { this.skills.set(s ?? []); this.loadIntegrationConfigs(); this.loading.set(false); },
          error: () => { this.skills.set([]); this.loading.set(false); },
        });
      },
    });
  }

  private loadAgentTools(): void {
    if (!this.agentId) return;
    this.api.getAgentTools(this.agentId).subscribe({
      next: (res) => {
        const tools: AgentTool[] = (res.enabled ?? []).map((t: string | { name: string; description?: string }) =>
          typeof t === 'string' ? { name: t } : { name: t.name, description: t.description },
        );
        this.agentTools.set(tools);
      },
      error: () => this.agentTools.set([]),
    });
  }

  loadSkills(): void {
    const handleSkills = (s: any[]) => {
      this.skills.set(s ?? []);
      for (const sk of s ?? []) {
        if (sk.name) this.loadSkillOnboarding(sk.name);
      }
      this.loadChannelStatuses(s ?? []);
      this.loadIntegrationConfigs();
    };

    if (this.agentId) {
      this.api.getAgentSkills(this.agentId).subscribe({
        next: handleSkills,
        error: () => {
          this.api.getSkills().subscribe({
            next: handleSkills,
            error: () => this.skills.set([]),
          });
        },
      });
    } else {
      this.api.getSkills().subscribe({
        next: handleSkills,
        error: () => this.skills.set([]),
      });
    }
  }

  private loadIntegrationConfigs(): void {
    const list = this.integrations();
    const cache: Record<string, { config: Record<string, unknown>; schema: ConfigFieldSchema[] }> = {};
    let pending = list.length;
    if (pending === 0) {
      this.integrationConfigCache.set(cache);
      return;
    }
    for (const s of list) {
      this.api.getSkillConfig(s.name, true, this.agentId || undefined).subscribe({
        next: (res: { config?: Record<string, unknown>; schema?: ConfigFieldSchema[] }) => {
          const config = { ...(res?.config ?? {}) };
          if (s.name === 'email-channel' && this.userEmail && !config['owner_identity']) {
            config['owner_identity'] = this.userEmail;
          }
          cache[s.name] = { config, schema: res?.schema ?? [] };
          pending--;
          if (pending === 0) {
            this.integrationConfigCache.set({ ...cache });
            this.syncStatusFromConfigs(cache);
          }
        },
        error: () => {
          pending--;
          if (pending === 0) {
            this.integrationConfigCache.set({ ...cache });
            this.syncStatusFromConfigs(cache);
          }
        },
      });
    }
  }

  /** Use config values as fallback to populate channel status signals. */
  private syncStatusFromConfigs(cache: Record<string, { config: Record<string, unknown>; schema: ConfigFieldSchema[] }>): void {
    const emailCfg = cache['email-channel']?.config;
    if (emailCfg?.['alias'] && !this.emailAlias()) {
      this.emailAlias.set(String(emailCfg['alias']));
    }

    const telegramCfg = cache['telegram-channel']?.config;
    if (telegramCfg?.['bot_token'] && !this.telegramConnected()) {
      this.telegramConnected.set(true);
      this.telegramBotUsername.set(String(telegramCfg['bot_username'] || ''));
    }

    const whatsappCfg = cache['whatsapp-channel']?.config;
    if (whatsappCfg?.['linked_phone'] && !this.whatsappConnected()) {
      this.whatsappConnected.set(true);
      this.whatsappPhone.set(String(whatsappCfg['linked_phone']));
    }

    const gwCfg = cache['google-workspace']?.config;
    if (gwCfg?.['connected_email'] && !this.googleWorkspaceConnected()) {
      this.googleWorkspaceConnected.set(true);
      this.googleWorkspaceEmail.set(String(gwCfg['connected_email']));
    }
  }

  // ── Integration helpers ──────────────────────────────────

  getChannelType(skillName: string): IntegrationChannelType {
    return CHANNEL_SKILL_NAMES[skillName] ?? 'email';
  }

  readonly usesBaboCloudBackend = usesBaboCloudBackend;

  getIntegrationContext(channel: IntegrationChannelId) {
    return buildIntegrationContext(
      channel,
      this.platformIntegrations.backendChoice(),
      this.platformIntegrations.capabilities(),
      this.platformIntegrations.nestjsUrl(),
    );
  }

  googleUsesByoCredentials(): boolean {
    return googleUsesByo(this.platformIntegrations.backendChoice());
  }

  getIntegrationStatus(skillName: string): { connected: boolean; displayValue?: string } | null {
    if (skillName === 'email-channel') {
      const alias = this.emailAlias();
      return { connected: !!alias, displayValue: alias || undefined };
    }
    if (skillName === 'telegram-channel') {
      const connected = this.telegramConnected();
      return { connected, displayValue: connected ? (this.telegramBotUsername() || 'Bot') : undefined };
    }
    if (skillName === 'whatsapp-channel') {
      const connected = this.whatsappConnected();
      return { connected, displayValue: connected ? (this.whatsappPhone() || '') : undefined };
    }
    if (skillName === 'google-workspace') {
      const connected = this.googleWorkspaceConnected();
      return { connected, displayValue: connected ? (this.googleWorkspaceEmail() || 'Connected') : undefined };
    }
    return null;
  }

  getIntegrationSchema(skillName: string): ConfigFieldSchema[] {
    return this.integrationConfigCache()[skillName]?.schema ?? [];
  }

  getIntegrationConfigValues(skillName: string): Record<string, unknown> {
    return this.integrationDrafts()[skillName] ?? this.integrationConfigCache()[skillName]?.config ?? {};
  }

  integrationConfigSaving(skillName: string): boolean {
    return !!this.integrationSaving()[skillName];
  }

  integrationConfigSaveSuccess(skillName: string): boolean {
    return !!this.integrationSaveSuccess()[skillName];
  }

  isConnecting(skillName: string): boolean {
    if (skillName === 'email-channel') return this.emailActivating();
    if (skillName === 'whatsapp-channel') return this.whatsappPairing();
    return false;
  }

  getReadOnlyKeys(skillName: string): Set<string> {
    const keys = new Set<string>();
    const schema = this.getIntegrationSchema(skillName);
    for (const f of schema) {
      if (f.category === 'connection' && (f.key === 'alias' || f.key === 'linked_phone' || f.key === 'connected_email')) {
        keys.add(f.key);
      }
    }
    return keys;
  }

  showCompleteSetupPrompt(skillName: string): boolean {
    const schema = this.getIntegrationSchema(skillName);
    const values = this.getIntegrationConfigValues(skillName);
    const required = schema.filter(f => f.required && f.category !== 'connection');
    return required.some(f => {
      const v = values[f.key];
      return v === undefined || v === null || v === '' || (Array.isArray(v) && v.length === 0);
    });
  }

  onIntegrationConfigChange(skillName: string, event: { key: string; value: unknown }): void {
    const current = this.getIntegrationConfigValues(skillName);
    this.integrationDrafts.update(d => ({
      ...d,
      [skillName]: { ...current, [event.key]: event.value },
    }));
  }

  saveIntegrationConfig(skillName: string, config: Record<string, unknown>): void {
    this.integrationSaving.update((m) => ({ ...m, [skillName]: true }));
    this.integrationSaveSuccess.update((m) => ({ ...m, [skillName]: false }));
    this.api.updateSkillConfig(skillName, config, this.agentId || undefined).subscribe({
      next: () => {
        this.integrationSaving.update((m) => ({ ...m, [skillName]: false }));
        this.integrationSaveSuccess.update((m) => ({ ...m, [skillName]: true }));
        this.integrationConfigCache.update((c) => ({
          ...c,
          [skillName]: { ...(c[skillName] ?? { config: {}, schema: [] }), config: { ...(c[skillName]?.config ?? {}), ...config } },
        }));
      },
      error: () => this.integrationSaving.update((m) => ({ ...m, [skillName]: false })),
    });
  }

  // ── Modal openers ──────────────────────────────────────

  openIntegrationModal(skillName: string): void {
    this.activeIntegration.set(skillName);
    void this.platformIntegrations.refresh();
    this.loadSkillOnboarding(skillName);
    this.loadChannelStatusForSkill(skillName);
  }

  quickConnect(skillName: string): void {
    if (skillName === 'email-channel') { this.connectEmail(skillName); return; }
    if (skillName === 'telegram-channel') { this.connectTelegram(); return; }
    if (skillName === 'google-workspace') { this.connectGoogleWorkspace(); return; }
    this.openIntegrationModal(skillName);
  }

  openSkillModal(skillName: string): void {
    this.activeSkill.set(skillName);
    this.skillTab.set('config');
    this.activeFile.set(null);
    this.loadSkillConfig(skillName);
    this.loadSkillFiles(skillName);
    this.loadSkillOnboarding(skillName);
  }

  closeSkillModal(): void {
    this.activeSkill.set(null);
  }

  // ── Skill helpers ──────────────────────────────────────

  getSkillError(skillName: string): string | undefined {
    return this.skills().find(s => s.name === skillName)?.error;
  }

  getSkillStatus(skillName: string): string {
    return this.skills().find(s => s.name === skillName)?.status ?? '';
  }

  getSkillOnboarding(name: string): any {
    return this.skillOnboarding()[name] || null;
  }

  // ── Channel statuses ──────────────────────────────────

  private loadChannelStatuses(skills: any[]): void {
    if (!this.agentId) return;
    const loaded = (name: string) => skills.find(s => s.name === name)?.status === 'loaded';
    const rid = this.runtimeAgentId || this.agentId;

    if (loaded('email-channel')) {
      this.http.get<any>(`${this.api.apiBase}/channels/email/status/${this.agentId}`).subscribe({
        next: (s) => { if (s?.alias) this.emailAlias.set(s.alias); },
        error: () => {},
      });
    }

    if (loaded('telegram-channel')) {
      this.http.get<any>(`${this.api.runtimeBase}/skills/telegram-channel/status/${rid}`).subscribe({
        next: (s) => {
          if (s?.connected) {
            this.telegramConnected.set(true);
            this.telegramBotUsername.set(s.bot_username || '');
          }
        },
        error: () => {},
      });
    }

    if (loaded('whatsapp-channel')) {
      this.http.get<any>(`${this.api.runtimeBase}/skills/whatsapp-channel/status/${rid}`).subscribe({
        next: (s) => {
          if (s?.connected) {
            this.whatsappConnected.set(true);
            this.whatsappPhone.set(s.linked_phone || '');
          }
        },
        error: () => {},
      });
    }

    if (loaded('google-workspace')) {
      this.http.get<any>(`${this.api.runtimeBase}/skills/google-workspace/status/${rid}`).subscribe({
        next: (s) => {
          if (s?.connected) {
            this.googleWorkspaceConnected.set(true);
            this.googleWorkspaceEmail.set(s.email || '');
          }
        },
        error: () => {},
      });
    }
  }

  private loadChannelStatusForSkill(skillName: string): void {
    if (!this.agentId) return;
    const rid = this.runtimeAgentId || this.agentId;
    const skill = this.skills().find(s => s.name === skillName);
    if (skill?.status !== 'loaded') return;

    if (skillName === 'email-channel') {
      this.http.get<any>(`${this.api.apiBase}/channels/email/status/${this.agentId}`).subscribe({
        next: (s) => { if (s?.alias) this.emailAlias.set(s.alias); },
        error: () => {},
      });
    }
    if (skillName === 'telegram-channel') {
      this.http.get<any>(`${this.api.runtimeBase}/skills/telegram-channel/status/${rid}`).subscribe({
        next: (s) => {
          if (s?.connected) {
            this.telegramConnected.set(true);
            this.telegramBotUsername.set(s.bot_username || '');
          }
        },
        error: () => {},
      });
    }
    if (skillName === 'whatsapp-channel') {
      this.http.get<any>(`${this.api.runtimeBase}/skills/whatsapp-channel/status/${rid}`).subscribe({
        next: (s) => {
          if (s?.connected) {
            this.whatsappConnected.set(true);
            this.whatsappPhone.set(s.linked_phone || '');
          }
        },
        error: () => {},
      });
    }
    if (skillName === 'google-workspace') {
      this.http.get<any>(`${this.api.runtimeBase}/skills/google-workspace/status/${rid}`).subscribe({
        next: (s) => {
          if (s?.connected) {
            this.googleWorkspaceConnected.set(true);
            this.googleWorkspaceEmail.set(s.email || '');
          }
        },
        error: () => {},
      });
    }
  }

  // ── Skill config / files ──────────────────────────────

  private loadSkillConfig(name: string): void {
    this.skillConfigLoading.set(true);
    this.skillConfigData.set({});
    this.skillConfigSchema.set([]);
    this.configSaveSuccess.set(false);
    this.api.getSkillConfig(name, true, this.agentId || undefined).subscribe({
      next: (res: { config?: Record<string, unknown>; schema?: ConfigFieldSchema[] } | Record<string, unknown>) => {
        const config = (res && 'config' in res && res.config) ? res.config : (res ?? {});
        const schema = (res && 'schema' in res && Array.isArray((res as any).schema)) ? (res as any).schema : [];
        this.skillConfigData.set(config as Record<string, unknown>);
        this.skillConfigSchema.set(schema);
        this.skillConfigLoading.set(false);
        if (name === 'email-channel' && (config as any)?.alias) {
          this.emailAlias.set((config as any).alias);
        }
      },
      error: () => { this.skillConfigData.set({}); this.skillConfigSchema.set([]); this.skillConfigLoading.set(false); },
    });
  }

  private loadSkillFiles(name: string): void {
    this.skillFiles.set([]);
    this.api.getSkill(name).subscribe({
      next: (detail: any) => {
        const files = (detail.files ?? []).map((f: any) => ({
          path: f.path,
          name: f.path.replace(/\\/g, '/').split('/').pop() || f.path,
          size: f.size,
        }));
        this.skillFiles.set(files);
      },
      error: () => this.skillFiles.set([]),
    });
  }

  private loadSkillOnboarding(name: string): void {
    this.http.get<any>(`${this.api.runtimeBase}/admin/skills/${name}/onboarding`).subscribe({
      next: (ob) => { this.skillOnboarding.update(prev => ({ ...prev, [name]: ob })); },
      error: () => {},
    });
  }

  onConfigChange(key: string, value: any): void {
    this.skillConfigData.update((prev) => ({ ...prev, [key]: value }));
    this.configSaveSuccess.set(false);
  }

  saveConfig(skillName: string): void {
    this.configSaving.set(true);
    this.configSaveSuccess.set(false);
    this.api.updateSkillConfig(skillName, this.skillConfigData(), this.agentId || undefined).subscribe({
      next: () => { this.configSaving.set(false); this.configSaveSuccess.set(true); },
      error: () => { this.configSaving.set(false); },
    });
  }

  openFile(skillName: string, filePath: string): void {
    this.activeFile.set(filePath);
    this.fileLoading.set(true);
    this.fileContent.set('');
    this.fileEditable.set(false);
    this.fileSaveSuccess.set(false);
    this.editedFileContent = '';
    this.api.getSkillFile(skillName, filePath).subscribe({
      next: (res) => {
        this.fileContent.set(res.content);
        this.editedFileContent = res.content;
        this.fileEditable.set(
          filePath.endsWith('.py') || filePath.endsWith('.json') || filePath.endsWith('.txt') || filePath.endsWith('.md'),
        );
        this.fileLoading.set(false);
      },
      error: () => { this.fileContent.set('Failed to load file.'); this.fileLoading.set(false); },
    });
  }

  onFileEdit(value: string): void {
    this.editedFileContent = value;
    this.fileSaveSuccess.set(false);
  }

  saveFile(skillName: string): void {
    const file = this.activeFile();
    if (!file) return;
    this.fileSaving.set(true);
    this.fileSaveSuccess.set(false);
    this.api.updateSkillFile(skillName, file, this.editedFileContent).subscribe({
      next: () => { this.fileSaving.set(false); this.fileSaveSuccess.set(true); this.fileContent.set(this.editedFileContent); },
      error: () => { this.fileSaving.set(false); },
    });
  }

  formatFileSize(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1048576).toFixed(1)} MB`;
  }

  // ── Skill actions ──────────────────────────────────────

  toggleSkillForAgent(name: string, enabled: boolean): void {
    if (!this.agentId) return;
    const call = enabled
      ? this.api.enableAgentSkill(this.agentId, name)
      : this.api.disableAgentSkill(this.agentId, name);
    call.subscribe({ next: () => this.loadSkills() });
  }

  enableSkill(name: string): void {
    this.api.enableSkill(name).subscribe({ next: () => { this.loadSkills(); this.closeSkillModal(); } });
  }

  disableSkill(name: string): void {
    this.api.disableSkill(name).subscribe({ next: () => { this.loadSkills(); this.closeSkillModal(); } });
  }

  deleteSkill(name: string): void {
    this.closeSkillModal();
    this.api.deleteSkill(name).subscribe({ next: () => this.loadSkills() });
  }

  // ── Reviews ──────────────────────────────────────────

  loadReviews(): void {
    this.api.getSkillReviews().subscribe({
      next: (reviews) => {
        this.pendingReviews.set((reviews ?? []).filter((r: any) => r.status === 'pending'));
      },
      error: () => this.pendingReviews.set([]),
    });
  }

  approveReview(id: string): void {
    if (this.reviewLoading[id]) return;
    this.reviewLoading[id] = 'approve';
    this.api.approveSkillReview(id).subscribe({
      next: () => this.loadReviews(),
      error: () => { delete this.reviewLoading[id]; },
    });
  }

  rejectReview(id: string): void {
    if (this.reviewLoading[id]) return;
    this.reviewLoading[id] = 'reject';
    this.api.rejectSkillReview(id).subscribe({
      next: () => { delete this.reviewLoading[id]; this.loadReviews(); this.loadSkills(); },
      error: () => { delete this.reviewLoading[id]; },
    });
  }

  // ── Channel connect actions ──────────────────────────

  connectEmail(skillName: string): void {
    if (!this.agentId) return;
    if (!this.emailChannelReady()) {
      this.toast.show(
        'Configure Resend in Settings → Integrations first',
        'error',
        5000,
      );
      return;
    }
    this.emailActivating.set(true);
    this.http.post<any>(`${this.api.apiBase}/channels/email/activate/${this.agentId}`, {}).subscribe({
      next: (res) => {
        this.emailActivating.set(false);
        this.emailAlias.set(res.alias || '');
        this.toast.show('Email channel activated!', 'info', 3000);
        if (res.alias) {
          this.http.post<any>(
            `${this.api.runtimeBase}/skills/email-channel/activate/${this.agentId}`,
            { alias: res.alias, from_address: res.from_address || res.alias },
          ).subscribe({ error: () => {} });
        }
        this.loadIntegrationConfigs();
      },
      error: (err) => {
        this.emailActivating.set(false);
        this.toast.show(err.error?.detail || 'Failed to activate email', 'error', 5000);
      },
    });
  }

  connectTelegram(): void {
    if (!this.agentId) return;
    this.router.navigate(['/chat', this.agentId], {
      queryParams: { setup: 'telegram-channel' },
    });
  }

  connectEmailInChat(): void {
    if (!this.agentId) return;
    this.router.navigate(['/chat', this.agentId], {
      queryParams: { setup: 'email-channel' },
    });
  }

  connectGoogleInChat(): void {
    if (!this.agentId) return;
    this.router.navigate(['/chat', this.agentId], {
      queryParams: { setup: 'google-workspace' },
    });
  }

  connectGoogleWorkspace(): void {
    if (!this.agentId) return;
    this.googleModalOpen.set(true);
  }

  onGoogleConnected(event: { email: string }): void {
    this.loadIntegrationConfigs();
  }

  closeGoogleModal(): void {
    this.googleModalOpen.set(false);
    this.loadIntegrationConfigs();
  }

  startWhatsAppPairing(): void {
    if (!this.agentId || this.whatsappPairing()) return;
    this.whatsappPairing.set(true);
    this.whatsappQR.set('');
    this.whatsappStatus.set('connecting');

    this.http.post<any>(
      `${this.api.runtimeBase}/skills/whatsapp-channel/pair/${this.agentId}`,
      {},
    ).subscribe({
      next: (res) => {
        this.whatsappPairing.set(false);
        if (res.status === 'already_connected') {
          this.whatsappConnected.set(true);
          this.whatsappPhone.set(res.phone || '');
          this.toast.show('WhatsApp already connected!', 'info', 3000);
          return;
        }
        if (res.qr) this.whatsappQR.set(res.qr);
        this.whatsappStatus.set(res.status || 'waiting');
        this.startWhatsAppQrPoll();
      },
      error: (err) => {
        this.whatsappPairing.set(false);
        this.toast.show(err.error?.detail || 'Failed to start pairing', 'error', 5000);
      },
    });
  }

  private startWhatsAppQrPoll(): void {
    this.stopWhatsAppQrPoll();
    this.whatsappQrPoller = setInterval(() => {
      this.http.get<any>(
        `${this.api.runtimeBase}/skills/whatsapp-channel/qr/${this.agentId}`,
      ).subscribe({
        next: (res) => {
          if (res.status === 'connected') {
            this.whatsappConnected.set(true);
            this.whatsappQR.set('');
            this.whatsappPhone.set(res.phone || '');
            this.whatsappStatus.set('connected');
            this.stopWhatsAppQrPoll();
            this.toast.show('WhatsApp connected!', 'info', 3000);
            return;
          }
          if (res.qr) this.whatsappQR.set(res.qr);
          this.whatsappStatus.set(res.status || 'waiting');
        },
      });
    }, 3000);
  }

  private stopWhatsAppQrPoll(): void {
    if (this.whatsappQrPoller) {
      clearInterval(this.whatsappQrPoller);
      this.whatsappQrPoller = null;
    }
  }

  // ── Repair ──────────────────────────────────────────

  async startRepair(skillName: string): Promise<void> {
    if (this.repairRunning()) return;

    this.repairRunning.set(true);
    this.repairSkillName.set(skillName);
    this.repairStep.set('Starting repair...');
    this.repairResult.set(null);

    const url = `${this.api.runtimeBase}/admin/skills/${encodeURIComponent(skillName)}/repair?agent_id=${encodeURIComponent(this.agentId)}`;

    try {
      const resp = await fetch(url, { method: 'POST' });
      if (!resp.ok || !resp.body) {
        this.repairStep.set(`Error: ${resp.statusText}`);
        this.repairResult.set('failed');
        this.repairRunning.set(false);
        return;
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          try {
            const event = JSON.parse(line.slice(6));
            this.handleRepairEvent(event);
          } catch { /* ignore */ }
        }
      }

      if (buffer.startsWith('data: ')) {
        try { this.handleRepairEvent(JSON.parse(buffer.slice(6))); } catch { /* ignore */ }
      }
    } catch (err: any) {
      this.repairStep.set(`Network error: ${err.message || err}`);
      this.repairResult.set('failed');
    } finally {
      this.repairRunning.set(false);
      this.cdr.detectChanges();
    }
  }

  private handleRepairEvent(event: any): void {
    switch (event.type) {
      case 'pass_start':
        this.repairStep.set(event.pass > 1 ? `Retry #${event.pass}...` : 'Analyzing error...');
        break;
      case 'step': {
        const tool = event.tool || '?';
        const file = event.file ? ` ${event.file}` : '';
        const labels: Record<string, string> = { read: `Reading${file}...`, write: `Writing${file}...`, edit: `Editing${file}...`, bash: 'Running test...' };
        this.repairStep.set(labels[tool] ?? `${tool}${file}...`);
        break;
      }
      case 'reloading':
        this.repairStep.set('Reloading skill...');
        break;
      case 'reload_failed':
        this.repairStep.set(`Reload failed: ${event.error}`);
        break;
      case 'complete':
        if (event.success) {
          this.repairStep.set('Fixed!');
          this.repairResult.set('success');
          this.loadSkills();
        } else {
          this.repairStep.set(event.error || 'Repair failed');
          this.repairResult.set('failed');
        }
        break;
    }
    this.cdr.detectChanges();
  }

  escalateToChat(skillName: string, error: string): void {
    const prefill = `Skill "${skillName}" has a load error: ${error}. Please fix it.`;
    this.router.navigate(['/chat', this.agentId], { queryParams: { prefill } });
  }

  // -- ClawHub --

  onClawhubSearch(query: string): void {
    this.clawhubQuery.set(query);
    this.clawhubError.set('');
    if (this.clawhubSearchTimer) clearTimeout(this.clawhubSearchTimer);
    if (!query.trim()) {
      this.mcpResults.set([]);
      if (this.sourceFilter() === 'extensions') {
        this.loadFeaturedExtensions();
      } else {
        this.loadFeaturedSkills(this.clawhubActiveCategory());
      }
      return;
    }
    this.clawhubActiveCategory.set('');
    this.clawhubSearchTimer = setTimeout(async () => {
      this.clawhubLoading.set(true);
      const promises: Promise<void>[] = [];
      if (this.sourceFilter() !== 'extensions') {
        promises.push(this.searchClawhub(query));
      }
      if (this.sourceFilter() !== 'skills') {
        promises.push(this.searchMcpExtensions(query));
      }
      await Promise.all(promises);
      this.clawhubLoading.set(false);
      this.cdr.detectChanges();
    }, 400);
  }

  browseClawhubCategory(cat: { label: string; sort: string }): void {
    this.clawhubQuery.set('');
    this.clawhubActiveCategory.set(cat.label);
    this.clawhubError.set('');
    this.mcpResults.set([]);
    this.loadFeaturedSkills(cat.label);
  }

  private async loadFeaturedSkills(categoryLabel: string, retry = 0): Promise<void> {
    const cat = this.clawhubCategories.find(c => c.label === categoryLabel);
    if (!cat) return;

    const cacheKey = `featured:${cat.sort}`;
    const cached = skillsCache.get(cacheKey);
    if (cached) {
      this.clawhubResults.set(cached);
      this.clawhubError.set('');
      this.cdr.detectChanges();
      return;
    }

    const stale = skillsCache.getStale(cacheKey);
    if (stale) this.clawhubResults.set(stale);

    this.clawhubLoading.set(!stale);
    this.clawhubError.set('');
    try {
      const res = await fetch(
        `${this.api.runtimeBase}/api/clawhub/featured?sort=${cat.sort}&limit=12`
      );
      if (res.ok) {
        const data = await res.json();
        skillsCache.set(cacheKey, data);
        this.clawhubResults.set(data);
      } else if (!stale) {
        if (retry < 1) {
          await new Promise(r => setTimeout(r, 1500));
          return this.loadFeaturedSkills(categoryLabel, retry + 1);
        }
        this.clawhubError.set('Could not load skills from ClawHub');
      }
    } catch {
      if (!stale) {
        if (retry < 1) {
          await new Promise(r => setTimeout(r, 1500));
          return this.loadFeaturedSkills(categoryLabel, retry + 1);
        }
        this.clawhubError.set('Could not connect to ClawHub');
      }
    } finally {
      this.clawhubLoading.set(false);
      this.cdr.detectChanges();
    }
  }

  private async searchClawhub(query: string): Promise<void> {
    const cacheKey = `search:${query.toLowerCase().trim()}`;
    const cached = skillsCache.get(cacheKey);
    if (cached) {
      this.clawhubResults.set(cached);
      return;
    }
    try {
      const res = await fetch(
        `${this.api.runtimeBase}/api/clawhub/search?q=${encodeURIComponent(query)}&limit=12`
      );
      if (res.ok) {
        const data = await res.json();
        skillsCache.set(cacheKey, data);
        this.clawhubResults.set(data);
      }
    } catch {
      const stale = skillsCache.getStale(cacheKey);
      if (stale) this.clawhubResults.set(stale);
    }
  }

  formatDownloads(n: number): string {
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
    if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
    return `${n}`;
  }

  async installClawhubSkill(slug: string): Promise<void> {
    this.clawhubInstalling.set(slug);
    try {
      const res = await fetch(`${this.api.runtimeBase}/api/clawhub/install`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ slug })
      });
      if (res.ok) {
        this.toast.show('Skill installed successfully', 'info');
        this.loadSkills();
        this.clawhubResults.update(arr => arr.filter(r => r.slug !== slug));
      } else if (res.status === 409) {
        this.toast.show('Skill already installed', 'info');
      } else {
        this.toast.show('Failed to install skill', 'error');
      }
    } catch {
      this.toast.show('Failed to install skill', 'error');
    }
    finally { this.clawhubInstalling.set(null); this.cdr.detectChanges(); }
  }

  // -- Source filter & community detail --

  setSourceFilter(filter: 'all' | 'skills' | 'extensions'): void {
    const prev = this.sourceFilter();
    this.sourceFilter.set(filter);
    if (!this.clawhubQuery()) {
      if (filter === 'extensions') {
        this.loadFeaturedExtensions();
      } else if (prev === 'extensions') {
        this.loadFeaturedSkills(this.clawhubActiveCategory() || 'Popular');
      }
    }
  }

  openCommunityDetail(item: any): void {
    this.communityDetailItem.set(item);
  }

  private async loadFeaturedExtensions(retry = 0): Promise<void> {
    const cacheKey = 'featured:*';
    const cached = extensionsCache.get(cacheKey);
    if (cached) {
      this.mcpResults.set(cached);
      this.clawhubError.set('');
      this.cdr.detectChanges();
      return;
    }

    const stale = extensionsCache.getStale(cacheKey);
    if (stale) this.mcpResults.set(stale);

    this.clawhubLoading.set(!stale);
    this.clawhubError.set('');
    try {
      const res = await fetch(
        `${this.api.runtimeBase}/skills/mcp-client/search?q=*`
      );
      if (res.ok) {
        const data = await res.json();
        const results = data.results ?? [];
        extensionsCache.set(cacheKey, results);
        this.mcpResults.set(results);
      } else if (!stale) {
        if (retry < 2) {
          await new Promise(r => setTimeout(r, 1500));
          return this.loadFeaturedExtensions(retry + 1);
        }
        this.clawhubError.set('Could not load extensions — the MCP registry may be unavailable');
        this.mcpResults.set([]);
      }
    } catch {
      if (!stale) {
        if (retry < 2) {
          await new Promise(r => setTimeout(r, 1500));
          return this.loadFeaturedExtensions(retry + 1);
        }
        this.clawhubError.set('Could not connect to extension registry');
        this.mcpResults.set([]);
      }
    } finally {
      this.clawhubLoading.set(false);
      this.cdr.detectChanges();
    }
  }

  // -- MCP Extensions --

  private async loadConnectedExtensions(): Promise<void> {
    try {
      const res = await fetch(`${this.api.runtimeBase}/skills/mcp-client/status`);
      if (res.ok) {
        const data = await res.json();
        this.connectedExtensions.set(data.servers ?? []);
      }
    } catch { /* MCP skill may not be running */ }
  }

  private async searchMcpExtensions(query: string): Promise<void> {
    const cacheKey = `search:${query.toLowerCase().trim()}`;
    const cached = extensionsCache.get(cacheKey);
    if (cached) {
      this.mcpResults.set(cached);
      return;
    }
    try {
      const res = await fetch(
        `${this.api.runtimeBase}/skills/mcp-client/search?q=${encodeURIComponent(query)}`
      );
      if (res.ok) {
        const data = await res.json();
        const results = data.results ?? [];
        extensionsCache.set(cacheKey, results);
        this.mcpResults.set(results);
      }
    } catch {
      const stale = extensionsCache.getStale(cacheKey);
      this.mcpResults.set(stale ?? []);
    }
  }

  async connectExtension(ext: any): Promise<void> {
    this.extensionConnecting.set(ext.name);
    try {
      const body: any = { name: ext.name };
      if (ext.url) body.url = ext.url;
      if (ext.command) {
        body.command = ext.command;
        if (ext.args) body.args = ext.args;
      }
      const res = await fetch(`${this.api.runtimeBase}/skills/mcp-client/connect`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (res.ok) {
        const data = await res.json();
        this.toast.show(`Connected to ${ext.name} (${data.tools} tools)`, 'info', 3000);
        this.loadConnectedExtensions();
        this.mcpResults.update(arr => arr.map(r =>
          r.name === ext.name ? { ...r, installed: true } : r
        ));
        const detail = this.communityDetailItem();
        if (detail && detail.name === ext.name) {
          this.communityDetailItem.set({ ...detail, installed: true });
        }
      } else {
        const err = await res.json().catch(() => ({}));
        this.toast.show(err.detail || `Failed to connect to ${ext.name}`, 'error', 5000);
      }
    } catch {
      this.toast.show(`Failed to connect to ${ext.name}`, 'error', 5000);
    } finally {
      this.extensionConnecting.set(null);
      this.cdr.detectChanges();
    }
  }

  async disconnectExtension(name: string): Promise<void> {
    this.extensionDisconnecting.set(name);
    try {
      const res = await fetch(`${this.api.runtimeBase}/skills/mcp-client/disconnect`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      });
      if (res.ok) {
        this.toast.show(`Disconnected from ${name}`, 'info', 3000);
        this.loadConnectedExtensions();
      }
    } catch {
      this.toast.show(`Failed to disconnect from ${name}`, 'error', 5000);
    } finally {
      this.extensionDisconnecting.set(null);
      this.cdr.detectChanges();
    }
  }

  // -- Brain tab --

  async openBrainTab(skillName: string): Promise<void> {
    this.skillTab.set('brain');
    this.brainLoading.set(true);
    this.brainData.set(null);
    try {
      const res = await fetch(`${this.api.runtimeBase}/admin/skills/${encodeURIComponent(skillName)}/brain`);
      if (res.ok) this.brainData.set(await res.json());
    } catch { /* ignore */ }
    finally { this.brainLoading.set(false); this.cdr.detectChanges(); }
  }
}
