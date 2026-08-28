import { Component, ElementRef, Input, computed, effect, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import {
  ChatWorkbenchService,
  WorkbenchEntry,
  WorkbenchLane,
} from '../../../core/services/chat-workbench.service';
import { ChatPanelService } from '../../../core/services/chat-panel.service';
import { AgentWorkspaceContextService } from '../../../core/services/agent-workspace-context.service';
import {
  fileChipParts,
  filePathChipLabel,
  isInvalidWorkspacePathToken,
} from '../../../core/services/activity-format.util';
import type { ActivityChip } from '../../../core/services/activity-format.util';
import { WorkspaceNavService } from '../../../core/services/workspace-nav.service';
import {
  bucketsForParallelGroup,
  groupWorkbenchEntries,
  parallelGroupTitle,
  type WorkbenchDisplayItem,
  type WorkbenchToolBucket,
} from '../../../core/services/workbench-display.util';
import {
  escalateHintForEntry,
  extractEntryErrorText,
  extractEntryOutputText,
  shouldShowErrorDetail,
} from '../../../core/services/workbench-error.util';
import { AnsiPipe } from '../../../shared/pipes/ansi.pipe';
import { TranslateModule, TranslateService } from '@ngx-translate/core';

export type WorkbenchTab = 'all' | 'chat' | 'background' | 'channel';

const REDUNDANT_TITLE_BY_TOOL: Record<string, RegExp> = {
  read: /^read file(\s|$|—|-)/i,
  write: /^write file/i,
  edit: /^edit file/i,
  delete: /^delete file/i,
  move: /^move file/i,
  list: /^list folder/i,
  glob: /^find files/i,
  grep: /^search in files/i,
  team: /^team (hint|inspect|launch|intervention|advance|create|disband)/i,
  plan: /^(create|read|update|archive|complete|continue|fix) plan/i,
  mode: /^switch mode/i,
};

@Component({
  selector: 'app-chat-workbench',
  standalone: true,
  imports: [CommonModule, AnsiPipe, TranslateModule],
  templateUrl: './chat-workbench.component.html',
  styleUrl: './chat-workbench.component.scss',
})
export class ChatWorkbenchComponent {
  @Input() agentId = '';

  private readonly host = inject(ElementRef<HTMLElement>);
  readonly workbench = inject(ChatWorkbenchService);
  private readonly panels = inject(ChatPanelService);
  private readonly workspaceNav = inject(WorkspaceNavService);
  private readonly workspaceCtx = inject(AgentWorkspaceContextService);
  private readonly translate = inject(TranslateService);

  readonly activeTab = signal<WorkbenchTab>('all');
  private prevFocusKey: string | null = null;

  readonly counts = computed(() => {
    const list = this.workbench.filteredEntries();
    let chat = 0;
    let background = 0;
    let channel = 0;
    for (const e of list) {
      if (e.surface) {
        channel++;
      } else if (e.lane === 'background') {
        background++;
      } else {
        chat++;
      }
    }
    return { all: list.length, chat, background, channel };
  });

  readonly visibleEntries = computed(() => {
    const list = this.workbench.filteredEntries();
    const sorted = [...list].sort((a, b) => b.ts - a.ts);
    const tab = this.activeTab();
    if (tab === 'all') {
      return sorted;
    }
    if (tab === 'channel') {
      return sorted.filter((e) => !!e.surface);
    }
    const lane: WorkbenchLane = tab === 'background' ? 'background' : 'chat';
    return sorted.filter((e) => !e.surface && e.lane === lane);
  });

  readonly displayItems = computed(() =>
    groupWorkbenchEntries(this.visibleEntries(), this.workbench.density()),
  );

  constructor() {
    effect(() => {
      const key = this.workbench.focusKey();
      const list = this.workbench.entries();

      if (key == null || key === '') {
        this.prevFocusKey = null;
        return;
      }

      if (key !== this.prevFocusKey) {
        this.prevFocusKey = key;
        for (let i = list.length - 1; i >= 0; i--) {
          if (list[i].correlationKey === key) {
            this.activeTab.set(list[i].lane === 'background' ? 'background' : 'chat');
            break;
          }
        }
      }

      queueMicrotask(() => this.scrollToCorrelation(key));
    });
  }

  setTab(tab: WorkbenchTab): void {
    this.activeTab.set(tab);
  }

  trackDisplayItem(_i: number, item: WorkbenchDisplayItem): string {
    return item.type === 'group' ? item.id : item.entry.id;
  }

  actorLabel(delegateNumber?: number): string {
    if (typeof delegateNumber === 'number' && delegateNumber >= 0) {
      return this.translate.instant('chat.workbench.sub', { n: delegateNumber });
    }
    return this.translate.instant('chat.workbench.orchestrator');
  }

  toolTagLabel(e: WorkbenchEntry): string {
    return (e.toolLabel || this.kindLabel(e.kind)).toUpperCase();
  }

  groupTitle(item: Extract<WorkbenchDisplayItem, { type: 'group' }>): string {
    return parallelGroupTitle(item.entries);
  }

  groupBuckets(item: Extract<WorkbenchDisplayItem, { type: 'group' }>): WorkbenchToolBucket[] {
    return bucketsForParallelGroup(item.entries);
  }

  /** First non-empty error text from a failed tool row in a parallel batch. */
  bucketErrorText(bucket: WorkbenchToolBucket): string | null {
    for (const e of bucket.entries) {
      if (e.status !== 'error') continue;
      const text = extractEntryErrorText(e);
      if (text) return text;
    }
    return null;
  }

  entryOutputText(e: WorkbenchEntry): string | null {
    return extractEntryOutputText(e);
  }

  entryErrorText(e: WorkbenchEntry): string | null {
    return extractEntryErrorText(e);
  }

  escalateHint(e: WorkbenchEntry, errorText: string | null): string | null {
    return escalateHintForEntry(e, errorText);
  }

  bucketEscalateHint(bucket: WorkbenchToolBucket, errorText: string | null): string | null {
    for (const e of bucket.entries) {
      if (e.status !== 'error') continue;
      const hint = escalateHintForEntry(e, errorText);
      if (hint) return hint;
    }
    return null;
  }

  showErrorDetail(e: WorkbenchEntry, errorText: string | null): boolean {
    return shouldShowErrorDetail(e, errorText);
  }

  isModeEntry(e: WorkbenchEntry): boolean {
    return e.toolLabel === 'Mode' || !!e.mode;
  }

  modeChips(e: WorkbenchEntry): ActivityChip[] | null {
    if (!this.isModeEntry(e)) return null;
    if (e.chips?.length) {
      const from = e.chips.find((c) => c.label === 'From');
      const to = e.chips.find((c) => c.label === 'To');
      if (from && to) return [from, to];
    }
    return null;
  }

  /** Routing / plan / team metadata chips (not preview bodies). */
  metaInlineChips(e: WorkbenchEntry): ActivityChip[] {
    return (e.chips ?? []).filter(
      (c) =>
        c.variant !== 'block'
        && c.label !== 'Preview'
        && c.label !== 'Result',
    );
  }

  /** Tool output body (shell-style gray block) — success, warning, or failure. */
  bodyBlocks(e: WorkbenchEntry): ActivityChip[] {
    const blocks = (e.chips ?? []).filter(
      (c) =>
        c.variant === 'block'
        || c.label === 'Preview'
        || c.label === 'Output'
        || c.label === 'Error'
        || c.label === 'Warning'
        || (c.label === 'Result' && (c.value?.length ?? 0) > 96),
    );
    const seen = new Set<string>();
    return blocks.filter((c) => {
      const v = (c.value || '').trim();
      if (!v) return false;
      if (seen.has(v)) return false;
      seen.add(v);
      return true;
    });
  }

  showCardTitle(e: WorkbenchEntry): boolean {
    const title = (e.title || '').trim();
    if (!title || this.isModeEntry(e)) return false;

    const toolKey = (e.toolLabel || '').toLowerCase();
    const pat = REDUNDANT_TITLE_BY_TOOL[toolKey];
    if (pat?.test(title)) return false;

    if (
      this.entryFilePaths(e).length
      && !this.metaInlineChips(e).length
      && !this.bodyBlocks(e).length
      && !this.showSubtitle(e)
    ) {
      return false;
    }

    return true;
  }

  showSubtitle(e: WorkbenchEntry): boolean {
    const sub = (e.subtitle || '').trim();
    if (!sub) return false;
    if (this.bodyBlocks(e).length > 0) return false;
    const chipText = (e.chips ?? [])
      .map((c) => (c.value || '').trim())
      .filter(Boolean);
    return !chipText.some(
      (v) => v === sub || sub.startsWith(v) || v.startsWith(sub),
    );
  }

  statusLabel(e: WorkbenchEntry): string | null {
    if (e.status === 'running') return this.translate.instant('chat.workbench.running');
    if (e.status === 'warn') return this.translate.instant('chat.workbench.warning');
    if (e.status === 'error') return this.translate.instant('chat.workbench.failed');
    return null;
  }

  close(): void {
    this.workbench.closePanel();
    this.panels.closeLeft();
  }

  densityLabel(): string {
    const d = this.workbench.density();
    return this.translate.instant(`chat.workbench.density.${d}`);
  }

  cycleDensity(): void {
    this.workbench.cycleDensity();
  }

  entryFilePaths(e: WorkbenchEntry): string[] {
    const raw = e.filePaths?.length
      ? e.filePaths
      : e.filePath
        ? [e.filePath]
        : [];
    return raw.filter(
      (p) => !!p?.trim() && !isInvalidWorkspacePathToken(p) && !p.endsWith('/'),
    );
  }

  fileLabel(path: string): string {
    return filePathChipLabel(path, this.workspaceCtx.getProjectDir(this.agentId));
  }

  fileParts(path: string): { parent: string; name: string } {
    return fileChipParts(path, this.workspaceCtx.getProjectDir(this.agentId));
  }

  openFile(path: string, event: Event): void {
    event.preventDefault();
    event.stopPropagation();
    const cleaned = (path || '').trim();
    if (!this.agentId || !cleaned || isInvalidWorkspacePathToken(cleaned)) {
      return;
    }
    this.workspaceNav.openFile(this.agentId, cleaned);
  }

  kindLabel(kind: string): string {
    switch (kind) {
      case 'agentic':
        return 'Task';
      case 'tool':
        return 'Tool';
      case 'activity':
        return 'Activity';
      default:
        return 'Log';
    }
  }

  toolTagClass(e: WorkbenchEntry): string {
    const label = (e.toolLabel || '').toLowerCase();
    if (e.mode) return 'wb-tag--mode';
    if (label === 'plan') return 'wb-tag--plan';
    if (label === 'team') return 'wb-tag--team';
    if (label === 'bash') return 'wb-tag--bash';
    if (label === 'read') return 'wb-tag--read';
    if (label === 'write' || label === 'edit') return 'wb-tag--write';
    if (label === 'delegate') return 'wb-tag--delegate';
    if (e.status === 'error') return 'wb-tag--err';
    if (e.status === 'warn') return 'wb-tag--warn';
    return 'wb-tag--tool';
  }

  private scrollToCorrelation(key: string): void {
    const root = this.host.nativeElement;
    const rows = root.querySelectorAll('[data-wb-corr]') as NodeListOf<HTMLElement>;
    let target: HTMLElement | null = null;
    for (let i = 0; i < rows.length; i++) {
      const el = rows.item(i);
      if (el.dataset['wbCorr'] === key) {
        target = el;
        break;
      }
    }
    if (target) {
      target.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
  }
}
