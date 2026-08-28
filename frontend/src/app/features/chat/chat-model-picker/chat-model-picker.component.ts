import {
  Component,
  inject,
  signal,
  computed,
  HostListener,
  ElementRef,
  viewChild,
  input,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { TranslateModule } from '@ngx-translate/core';
import { AgentModelService } from '../../../core/services/agent-model.service';
import type { ModelPickerOption } from '../../../core/services/agent-model.service';
import {
  filterModelPickerOptions,
  partitionModelPickerOptions,
} from '../../../core/services/model-catalog.util';

type MenuView =
  | { mode: 'empty'; empty: true }
  | { mode: 'flat'; empty: false; options: ModelPickerOption[] }
  | {
      mode: 'grouped';
      empty: false;
      local: ModelPickerOption[];
      featured: ModelPickerOption[];
      more: ModelPickerOption[];
    };

@Component({
  selector: 'app-chat-model-picker',
  standalone: true,
  imports: [CommonModule, TranslateModule],
  template: `
    @if (models.showPicker()) {
      <div
        class="model-picker"
        [class.wide]="mode() === 'creation' && variant() === 'default'"
        [class.chip]="variant() === 'chip'"
        [class.align-right]="align() === 'right'"
        (click)="$event.stopPropagation()"
      >
        <button
          type="button"
          class="model-trigger"
          (click)="toggleOpen()"
          [attr.aria-expanded]="open()"
          [title]="triggerTitle()"
        >
          <span class="model-trigger-label">{{ triggerLabel() }}</span>
          @if (variant() === 'chip' && showSplitBadge()) {
            <span class="split-badge" title="Split orchestrator / sub-agent">split</span>
          } @else if (variant() !== 'chip' && mode() === 'creation' && !models.creationHasCustomOrchestrator()) {
            <span class="default-chip">default</span>
          } @else if (models.hasRequestOverride()) {
            <span class="model-override-dot" title="One-shot override for next message"></span>
          } @else if (models.hasSessionDefault()) {
            <span class="model-session-dot" title="Agent session default"></span>
          }
          <svg class="chevron" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="6 9 12 15 18 9"></polyline>
          </svg>
        </button>
        @if (open()) {
          <div
            class="context-menu-panel model-menu"
            [class.menu-down]="menuPlacement() === 'down'"
            [class.menu-up]="menuPlacement() === 'up'"
            role="listbox"
            (click)="$event.stopPropagation()"
          >
            @if (showTargetTabs()) {
              <div class="context-menu-tabs" role="tablist">
                <button
                  type="button"
                  class="context-menu-tab"
                  [class.active]="pickTarget() === 'orchestrator'"
                  (click)="setPickTarget('orchestrator')"
                >
                  Orchestrator
                </button>
                <button
                  type="button"
                  class="context-menu-tab"
                  [class.active]="pickTarget() === 'delegate'"
                  (click)="setPickTarget('delegate')"
                >
                  Sub-agents
                </button>
              </div>
              <p class="context-menu-hint">
                Choosing model for
                <strong>{{ pickTarget() === 'delegate' ? 'sub-agents' : 'orchestrator' }}</strong>
              </p>
            }

            <div class="context-menu-search-wrap">
              <svg class="search-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
                <circle cx="11" cy="11" r="8"></circle>
                <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
              </svg>
              <input
                #searchInput
                type="search"
                class="model-search context-menu-search"
                [placeholder]="'chat.model.search' | translate"
                [value]="searchQuery()"
                (input)="onSearchInput($event)"
                (keydown.escape)="onSearchEscape($event)"
                autocomplete="off"
                spellcheck="false"
              />
            </div>

            <div class="model-list" (wheel)="onListWheel($event)">
              @if (menuView(); as view) {
                @switch (view.mode) {
                  @case ('empty') {
                    <p class="model-empty">{{ 'chat.model.noMatch' | translate }}</p>
                  }
                  @case ('flat') {
                    @for (opt of view.options; track opt.id) {
                      <button
                        type="button"
                        class="context-menu-item context-menu-row model-option"
                        [class.active]="activeListModelId() === opt.id"
                        (click)="select(opt.id)"
                      >
                        <span class="model-option-label">{{ opt.label }}</span>
                        @if (opt.id === models.defaultModelId()) {
                          <span class="default-tag">default</span>
                        }
                      </button>
                    }
                  }
                  @case ('grouped') {
                    @if (view.local.length) {
                      <p class="context-menu-title">{{ localSectionTitle() }}</p>
                      @for (opt of view.local; track opt.id) {
                        <button
                          type="button"
                          class="context-menu-item context-menu-row model-option"
                          [class.active]="activeListModelId() === opt.id"
                          (click)="select(opt.id)"
                        >
                          <span class="model-option-label">{{ opt.label }}</span>
                          @if (opt.id === models.defaultModelId()) {
                            <span class="default-tag">default</span>
                          }
                        </button>
                      }
                    }
                    @if (view.featured.length) {
                      @if (view.local.length) {
                        <div class="context-menu-sep" role="separator"></div>
                      }
                      <p class="context-menu-title">Popular</p>
                      @for (opt of view.featured; track opt.id) {
                        <button
                          type="button"
                          class="context-menu-item context-menu-row model-option"
                          [class.active]="activeListModelId() === opt.id"
                          (click)="select(opt.id)"
                        >
                          <span class="model-option-label">{{ opt.label }}</span>
                          @if (opt.id === models.defaultModelId()) {
                            <span class="default-tag">default</span>
                          }
                        </button>
                      }
                    }
                    @if (view.more.length) {
                      @if (view.local.length || view.featured.length) {
                        <div class="context-menu-sep" role="separator"></div>
                      }
                      <p class="context-menu-title">More models</p>
                      @for (opt of view.more; track opt.id) {
                        <button
                          type="button"
                          class="context-menu-item context-menu-row model-option"
                          [class.active]="activeListModelId() === opt.id"
                          (click)="select(opt.id)"
                        >
                          <span class="model-option-label">{{ opt.label }}</span>
                          @if (opt.id === models.defaultModelId()) {
                            <span class="default-tag">default</span>
                          }
                        </button>
                      }
                    }
                  }
                }
              }
            </div>

            <div class="model-footer">
              @if (mode() === 'chat') {
                <button type="button" class="model-footer-btn" (click)="setAsAgentDefault()">
                  Set as agent default
                </button>
                @if (models.hasSessionDefault()) {
                  <button type="button" class="model-footer-btn muted" (click)="clearAgentDefault()">
                    Clear agent default
                  </button>
                }
                @if (models.hasRequestOverride()) {
                  <button type="button" class="model-footer-btn muted" (click)="useDefault()">
                    Clear one-shot override
                  </button>
                }
              } @else {
                <p class="model-creation-hint">This becomes the agent's default orchestrator model.</p>
              }
              <button type="button" class="model-advanced-toggle" (click)="models.toggleAdvancedMode()">
                {{ models.advancedMode() ? 'Hide' : 'Advanced' }} orchestrator / sub-agent
              </button>
              @if (models.advancedMode()) {
                <label class="model-lock-row">
                  <input
                    type="checkbox"
                    [checked]="models.delegateLockToOrchestrator()"
                    (change)="onDelegateLockChange($event)"
                  />
                  Lock sub-agents to orchestrator model
                </label>
                @if (!models.delegateLockToOrchestrator()) {
                  <p class="model-hint">
                    Use the tabs above the list to pick orchestrator vs sub-agent models.
                  </p>
                }
              }
            </div>
          </div>
        }
      </div>
    }
  `,
  styles: [
    `
      .model-picker {
        position: relative;
        flex-shrink: 0;
        display: block;
        width: fit-content;
        max-width: 100%;
      }
      .model-picker .context-menu-panel.model-menu {
        z-index: var(--context-menu-z);
        pointer-events: auto;
      }
      .model-picker.wide .model-trigger {
        max-width: 100%;
        width: 100%;
        justify-content: space-between;
      }
      .model-picker.chip .model-trigger {
        max-width: min(220px, 42vw);
        padding: 6px 12px;
        background: var(--glass-bg);
        box-shadow: var(--shadow-glass);
      }
      .model-picker.align-right .model-menu {
        left: auto;
        right: 0;
      }
      .split-badge {
        flex-shrink: 0;
        font-size: 9px;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: var(--accent-primary);
        padding: 1px 5px;
        border-radius: 999px;
        background: var(--accent-primary-glow);
      }
      .model-trigger {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 4px 10px;
        border-radius: 999px;
        border: 1px solid var(--glass-border);
        background: var(--overlay-1);
        color: var(--text-secondary);
        font-size: 12px;
        cursor: pointer;
        max-width: 180px;
      }
      .model-trigger:hover {
        color: var(--text-primary);
        border-color: var(--glass-border-strong);
      }
      .model-trigger-label {
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .model-override-dot {
        width: 6px;
        height: 6px;
        border-radius: 50%;
        background: var(--accent-primary);
        flex-shrink: 0;
      }
      .model-session-dot {
        width: 6px;
        height: 6px;
        border-radius: 50%;
        background: var(--accent-success);
        flex-shrink: 0;
      }
      .default-chip {
        flex-shrink: 0;
        font-size: 10px;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        color: var(--text-muted);
        padding: 1px 6px;
        border-radius: 999px;
        background: var(--overlay-2);
      }
      .chevron {
        flex-shrink: 0;
        opacity: 0.6;
      }
      .model-menu {
        position: absolute;
        bottom: calc(100% + 8px);
        left: 0;
        min-width: 260px;
        max-width: min(320px, calc(100vw - 24px));
        max-height: min(360px, 50vh);
        display: flex;
        flex-direction: column;
        overflow: hidden;
        padding: 6px;
      }
      .model-menu .context-menu-tabs,
      .model-menu .context-menu-hint,
      .model-menu .context-menu-search-wrap,
      .model-footer {
        flex-shrink: 0;
      }
      .model-menu.menu-down {
        bottom: auto;
        top: calc(100% + 8px);
      }
      .model-list {
        overflow-y: auto;
        overflow-x: hidden;
        flex: 0 1 auto;
        min-height: 0;
        max-height: min(260px, calc(50vh - 120px));
        margin: 0 -2px;
        padding: 0 2px;
        overscroll-behavior: contain;
        -webkit-overflow-scrolling: touch;
      }
      .model-empty {
        margin: 8px;
        font-size: 12px;
        color: var(--text-muted);
        text-align: center;
      }
      .model-option {
        font-size: 13px;
        cursor: pointer;
        pointer-events: auto;
      }
      .model-option-label {
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .default-tag {
        flex-shrink: 0;
        font-size: 10px;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        color: var(--text-muted);
      }
      .model-reset,
      .model-footer {
        width: 100%;
        margin-top: 4px;
        padding-top: 4px;
        border-top: 1px solid var(--overlay-2);
        flex-shrink: 0;
        display: flex;
        flex-direction: column;
        gap: 2px;
      }
      .model-footer-btn,
      .model-advanced-toggle {
        width: 100%;
        padding: 8px 10px;
        border: none;
        background: transparent;
        color: var(--accent-primary);
        font-size: 12px;
        cursor: pointer;
        text-align: left;
      }
      .model-footer-btn.muted,
      .model-advanced-toggle {
        color: var(--text-secondary);
      }
      .model-footer-btn:hover,
      .model-advanced-toggle:hover {
        background: var(--overlay-1);
        border-radius: 8px;
      }
      .model-lock-row {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 6px 10px;
        font-size: 12px;
        color: var(--text-secondary);
        cursor: pointer;
      }
      .model-hint {
        margin: 0 10px 4px;
        font-size: 11px;
        color: var(--text-muted);
      }
      .model-creation-hint {
        margin: 0 10px 4px;
        font-size: 11px;
        color: var(--text-muted);
        line-height: 1.4;
      }
    `,
  ],
})
export class ChatModelPickerComponent {
  readonly mode = input<'chat' | 'creation'>('chat');
  readonly variant = input<'default' | 'chip'>('default');
  readonly align = input<'left' | 'right'>('left');
  readonly menuPlacement = input<'up' | 'down'>('up');
  readonly models = inject(AgentModelService);
  readonly open = signal(false);
  readonly searchQuery = signal('');
  readonly pickTarget = signal<'orchestrator' | 'delegate'>('orchestrator');
  private readonly el = inject(ElementRef);
  private readonly searchInput = viewChild<ElementRef<HTMLInputElement>>('searchInput');

  readonly showTargetTabs = computed(
    () => this.models.advancedMode() && !this.models.delegateLockToOrchestrator(),
  );

  readonly activeListModelId = computed(() => {
    if (this.showTargetTabs() && this.pickTarget() === 'delegate') {
      return this.models.effectiveDelegateModelId();
    }
    return this.models.effectiveModelId();
  });

  readonly menuView = computed((): MenuView => {
    const all = this.models.pickerOptions();
    const def = this.models.defaultModelId();
    const q = this.searchQuery().trim();
    const filtered = filterModelPickerOptions(all, q);

    if (!filtered.length) {
      return { mode: 'empty', empty: true };
    }

    if (q) {
      const sorted = [...filtered].sort((a, b) =>
        a.label.localeCompare(b.label, undefined, { sensitivity: 'base' }),
      );
      return { mode: 'flat', empty: false, options: sorted };
    }

    const { local, featured, more } = partitionModelPickerOptions(filtered, def, {
      tier: this.models.inferenceTier(),
    });
    if (!local.length && !featured.length && !more.length) {
      return { mode: 'flat', empty: false, options: filtered };
    }
    return { mode: 'grouped', empty: false, local, featured, more };
  });

  localSectionTitle(): string {
    const tier = this.models.inferenceTier();
    if (tier === 'self_lan') return 'LAN inference';
    if (tier === 'self_local') return 'Local inference';
    return 'Your server';
  }

  onListWheel(ev: WheelEvent): void {
    const el = ev.currentTarget as HTMLElement;
    if (el.scrollHeight > el.clientHeight + 1) {
      ev.stopPropagation();
    }
  }

  toggleOpen(): void {
    const next = !this.open();
    this.open.set(next);
    if (next) {
      void this.models.refreshFromConfig();
      this.searchQuery.set('');
      if (this.showTargetTabs()) {
        this.pickTarget.set('delegate');
      } else {
        this.pickTarget.set('orchestrator');
      }
      queueMicrotask(() => this.searchInput()?.nativeElement.focus());
    }
  }

  showSplitBadge(): boolean {
    return this.showTargetTabs()
      && this.models.effectiveDelegateModelId() !== this.models.effectiveModelId();
  }

  triggerLabel(): string {
    if (this.variant() === 'chip' && this.showSplitBadge()) {
      const orch = this.models.labelFor(this.models.effectiveModelId());
      const del = this.models.labelFor(this.models.effectiveDelegateModelId());
      return `${orch} / ${del}`;
    }
    return this.models.effectiveModelLabel();
  }

  triggerTitle(): string {
    if (this.mode() === 'creation') {
      return 'Default inference model for this agent';
    }
    return 'Model for this agent / message';
  }

  setPickTarget(target: 'orchestrator' | 'delegate'): void {
    this.pickTarget.set(target);
  }

  onSearchInput(ev: Event): void {
    const value = (ev.target as HTMLInputElement).value;
    this.searchQuery.set(value);
  }

  onSearchEscape(ev: Event): void {
    ev.stopPropagation();
    if (this.searchQuery()) {
      this.searchQuery.set('');
      return;
    }
    this.open.set(false);
  }

  select(modelId: string): void {
    const route = this.models.routeForCatalogId(modelId) ?? undefined;
    const delegatePick =
      this.showTargetTabs() && this.pickTarget() === 'delegate';

    if (delegatePick) {
      if (this.mode() === 'creation') {
        this.models.setCreationDelegateModel(modelId, route);
      } else {
        void this.models.setSessionDelegateModel(modelId, route);
      }
      this.closeMenu();
      return;
    }

    if (this.mode() === 'creation') {
      this.models.setCreationOrchestratorModel(modelId, route);
      this.closeMenu();
      return;
    }
    this.models.setChatModel(modelId, route);
    this.closeMenu();
  }

  async setAsAgentDefault(): Promise<void> {
    const id = this.models.effectiveModelId();
    const route = this.models.routeForCatalogId(id) ?? undefined;
    await this.models.setSessionOrchestratorModel(id, route);
    this.models.resetChatModel();
    this.closeMenu();
  }

  async clearAgentDefault(): Promise<void> {
    await this.models.setSessionOrchestratorModel(null);
    this.closeMenu();
  }

  async onDelegateLockChange(ev: Event): Promise<void> {
    const checked = (ev.target as HTMLInputElement).checked;
    if (checked) {
      this.pickTarget.set('orchestrator');
    } else {
      this.pickTarget.set('delegate');
    }
    if (this.mode() === 'creation') {
      this.models.setCreationDelegateLock(checked);
      return;
    }
    await this.models.setDelegateLockToOrchestrator(checked);
  }

  useDefault(): void {
    this.models.resetChatModel();
    this.closeMenu();
  }

  private closeMenu(): void {
    this.open.set(false);
    this.searchQuery.set('');
  }

  @HostListener('document:click', ['$event'])
  onDocumentClick(ev: MouseEvent): void {
    if (!this.open()) return;
    if (!this.el.nativeElement.contains(ev.target)) {
      this.closeMenu();
    }
  }
}
