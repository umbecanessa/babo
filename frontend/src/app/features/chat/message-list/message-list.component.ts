import {
  Component, Input, Output, EventEmitter, ElementRef,
  ViewChild, AfterViewChecked, HostListener, signal,
  OnChanges, SimpleChanges, OnDestroy,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ChatMessage, AgenticToolCall, AgenticToolResult, MessageAttachment } from '../../../core/services/websocket.service';
import {
  SignalTag,
  parseTags as _parseTags,
  stripTags as _stripTags,
  tagColor as _tagColor,
  humanType as _humanType,
  parseStreamingThinking,
} from '../../../shared/signal-utils';
import { MarkdownPipe } from '../../../shared/pipes/markdown.pipe';
import { ApiService } from '../../../core/services/api.service';
import { WebSocketService } from '../../../core/services/websocket.service';
import { ChatWorkbenchService } from '../../../core/services/chat-workbench.service';

// Re-export for template use
export type { SignalTag };

@Component({
  selector: 'app-message-list',
  standalone: true,
  imports: [CommonModule, FormsModule, MarkdownPipe],
  templateUrl: './message-list.component.html',
  styleUrl: './message-list.component.scss',
})
export class MessageListComponent implements OnChanges, AfterViewChecked, OnDestroy {
  @Input() messages: ChatMessage[] = [];
  @Input() streamingText = '';
  @Input() streamingReasoning = '';
  @Input() agenticActive = false;
  @Input() agentId = '';
  @Output() drowsyAction = new EventEmitter<'confirm' | 'deny'>();
  @Output() expandBrowser = new EventEmitter<any>();
  @Output() openUrl = new EventEmitter<string>();
  @Output() delegateToggle = new EventEmitter<number>();
  @Output() messageFeedback = new EventEmitter<{
    messageIndex: number;
    content: string;
    messageContent: string;
    feedbackType: 'SPECIFIC' | 'GLOBAL';
    channel?: string;
    sessionKey?: string;
  }>();
  @ViewChild('scrollContainer') scrollContainer!: ElementRef;

  generationElapsed = signal(0);
  private _genTimerHandle: ReturnType<typeof setInterval> | null = null;
  private _genStartedAt = 0;

  feedbackMessageIndex = signal<number | null>(null);
  feedbackText = '';
  feedbackType = signal<'SPECIFIC' | 'GLOBAL'>('SPECIFIC');
  feedbackSubmitting = signal(false);

  expandedDriveActions = new Set<number>();
  expandedTags = new Set<number>();
  expandedReasoning = new Set<number>();
  private _manuallyToggledReasoning = new Set<number>();
  streamReasoningExpanded = false;
  liveReasoningExpanded = false;
  expandedAgenticCards = new Set<number>();
  private _manuallyToggledAgentic = new Set<number>();
  expandedThinking = new Set<number>();
  drowsyResponded = new Set<number>();
  reviewResolved: Record<string, 'approved' | 'rejected'> = {};
  reviewLoading: Record<string, boolean> = {};

  constructor(
    private api: ApiService,
    private ws: WebSocketService,
    private workbench: ChatWorkbenchService,
  ) {}

  /** Return a display-friendly file type label from a MIME type. */
  attachmentIcon(mimeType: string): 'image' | 'doc' | 'table' | 'code' | 'file' {
    if (mimeType.startsWith('image/')) return 'image';
    if (mimeType === 'application/pdf' || mimeType.includes('word')) return 'doc';
    if (mimeType.includes('sheet') || mimeType.includes('excel') || mimeType === 'text/csv') return 'table';
    if (mimeType.startsWith('text/')) return 'code';
    return 'file';
  }

  /** Open bottom workbench; optional key aligns with workbench entry correlationKey when present. */
  openWorkbench(correlationKey?: string): void {
    if (correlationKey) {
      this.workbench.focusCorrelation(correlationKey);
    } else {
      this.workbench.openPanel();
    }
  }

  workbenchKeyForAssistant(msg: ChatMessage): string | undefined {
    const step = (msg as any).agenticStep as number | undefined;
    if (step != null && step > 0) {
      return `step-${step}`;
    }
    return undefined;
  }

  /** Only scroll when transcript inputs change — not on every CD (clicks/selection used to jump to bottom). */
  private scrollToBottomAfterView = false;

  ngOnChanges(changes: SimpleChanges): void {
    if (
      changes['messages'] ||
      changes['streamingText'] ||
      changes['streamingReasoning'] ||
      changes['agenticActive']
    ) {
      this.scrollToBottomAfterView = true;
    }
    this._updateGenerationTimer();
  }

  ngOnDestroy(): void {
    this._clearGenTimer();
  }

  ngAfterViewChecked(): void {
    if (!this.scrollToBottomAfterView) return;
    this.scrollToBottomAfterView = false;
    this.scrollToBottom();
  }

  /** True when the generation indicator should display. */
  get showGenerationIndicator(): boolean {
    return this.agenticActive
      && !this.streamingText?.trim()
      && !this.streamingReasoning?.trim()
      && this.isLastMessageAgenticIteration();
  }

  get generationLabel(): string {
    const s = this.generationElapsed();
    if (s < 10) return 'Planning next step\u2026';
    if (s < 60) return `Crunching data\u2026 (${s}s)`;
    const m = Math.floor(s / 60);
    const rem = s % 60;
    return `Crunching data\u2026 (${m}m ${rem}s)`;
  }

  private _updateGenerationTimer(): void {
    const shouldRun = this.showGenerationIndicator;
    if (shouldRun && !this._genTimerHandle) {
      this._genStartedAt = Date.now();
      this.generationElapsed.set(0);
      this._genTimerHandle = setInterval(() => {
        this.generationElapsed.set(
          Math.floor((Date.now() - this._genStartedAt) / 1000),
        );
      }, 1000);
    } else if (!shouldRun && this._genTimerHandle) {
      this._clearGenTimer();
    }
  }

  private _clearGenTimer(): void {
    if (this._genTimerHandle) {
      clearInterval(this._genTimerHandle);
      this._genTimerHandle = null;
    }
    this.generationElapsed.set(0);
  }

  toggleDriveExpand(index: number): void {
    if (this.expandedDriveActions.has(index)) {
      this.expandedDriveActions.delete(index);
    } else {
      this.expandedDriveActions.add(index);
    }
  }

  isDriveExpanded(index: number): boolean {
    return this.expandedDriveActions.has(index);
  }

  toggleTags(index: number): void {
    if (this.expandedTags.has(index)) {
      this.expandedTags.delete(index);
    } else {
      this.expandedTags.add(index);
    }
  }

  isTagsExpanded(index: number): boolean {
    return this.expandedTags.has(index);
  }

  driveActionLabel(actionType: string): string {
    const labels: Record<string, string> = {
      web_search: 'Browsing',
      wikipedia: 'Reading Wikipedia',
      read_page: 'Reading',
      deep_browse: 'Deep Browsing',
      self_test: 'Self-testing',
      self_check: 'Self-checking',
      reach_out: 'Reaching out',
      reflect: 'Reflecting',
    };
    return labels[actionType] || actionType;
  }

  /** Extract signal tags from a message and return the cleaned text + tags. */
  parseTags(content: string): { text: string; tags: SignalTag[] } {
    return _parseTags(content);
  }

  /** Strip tags from streaming text (no tag pills during streaming). */
  stripTags(content: string): string {
    return _stripTags(content);
  }

  /** Get the dot color for a signal type. */
  tagColor(type: string): string {
    return _tagColor(type);
  }

  /** Human-readable type label. */
  humanType(type: string): string {
    return _humanType(type);
  }

  /** Handle drowsy action button click. */
  onDrowsyAction(index: number, action: 'confirm' | 'deny'): void {
    this.drowsyResponded.add(index);
    this.drowsyAction.emit(action);
  }

  /** Check if a drowsy message has been responded to. */
  isDrowsyResponded(index: number): boolean {
    return this.drowsyResponded.has(index);
  }

  // ─── Reasoning / Thinking ──────────────────────────────────

  /** Toggle reasoning expansion for a message. */
  toggleReasoning(index: number): void {
    this._manuallyToggledReasoning.add(index);
    if (this.expandedReasoning.has(index)) {
      this.expandedReasoning.delete(index);
    } else {
      this.expandedReasoning.add(index);
    }
  }

  /** Check if reasoning is expanded for a message. */
  isReasoningExpanded(index: number): boolean {
    if (this._manuallyToggledReasoning.has(index)) {
      return this.expandedReasoning.has(index);
    }
    return false;
  }

  /** Toggle thinking expansion for a turn_thinking message. */
  toggleThinking(index: number): void {
    if (this.expandedThinking.has(index)) {
      this.expandedThinking.delete(index);
    } else {
      this.expandedThinking.add(index);
    }
  }

  /** Check if thinking is expanded for a turn_thinking message. */
  isThinkingExpanded(index: number): boolean {
    return this.expandedThinking.has(index);
  }

  /** Get a short snippet of reasoning text for the collapsed preview. */
  reasoningSnippet(text: string, maxLen = 120): string {
    const clean = text.replace(/\n+/g, ' ').trim();
    if (clean.length <= maxLen) return clean;
    return clean.slice(0, maxLen) + '...';
  }

  private _isLatestReasoningMessage(index: number): boolean {
    const msg = this.messages[index];
    if (msg?.type !== 'assistant' || !(msg as any).reasoning) return false;
    for (let i = this.messages.length - 1; i > index; i--) {
      const m = this.messages[i];
      if (m.type === 'assistant' && (m as any).reasoning) return false;
    }
    return true;
  }

  /** Parse streaming text to separate thinking from response. */
  parseStreamThinking(text: string) {
    return parseStreamingThinking(text);
  }

  /** Derive a status-aware CSS class for the status dot. */
  statusDotClass(content: string): string {
    const lower = content.toLowerCase();
    if (lower.includes('connected') || lower.includes('alive')) return 'dot-green';
    if (lower.includes('sleeping') || lower.includes('sleep')) return 'dot-amber';
    if (lower.includes('error') || lower.includes('disconnect')) return 'dot-red';
    return 'dot-neutral';
  }

  // ─── Agentic iteration cards ──────────────────────────────

  toggleAgenticExpand(index: number): void {
    this._manuallyToggledAgentic.add(index);
    if (this.expandedAgenticCards.has(index)) {
      this.expandedAgenticCards.delete(index);
    } else {
      this.expandedAgenticCards.add(index);
    }
  }

  isAgenticExpanded(index: number): boolean {
    if (this._manuallyToggledAgentic.has(index)) {
      return this.expandedAgenticCards.has(index);
    }
    return true;
  }

  getTaskSteps(completeIndex: number): ChatMessage[] {
    // Try backward walk through messages for live session
    const steps: ChatMessage[] = [];
    for (let i = completeIndex - 1; i >= 0; i--) {
      const m = this.messages[i];
      const t = m.type as string;
      if (t === 'agentic_iteration') {
        steps.unshift(m);
      } else if (t === 'agentic_start') {
        break;
      } else if (t === 'user' || t === 'assistant' || t === 'agentic_complete') {
        break;
      }
    }
    if (steps.length > 0) return steps;

    // Fallback: synthesize from agenticComplete.events (works after restore)
    const complete = this.messages[completeIndex];
    const events = complete?.agenticComplete?.events;
    if (!Array.isArray(events) || events.length === 0) return [];
    const total = complete.agenticComplete?.totalSteps || events.length;
    return events.map((ev: any) => {
      const names = (ev.toolCalls || []).map((tc: any) => tc.name || 'tool').join(', ');
      const results = ev.toolResults || [];
      const ok = results.filter((r: any) => r.success).length;
      return {
        type: 'agentic_iteration' as any,
        content: `Step ${ev.step}: ${names || 'processing'} (${ok}/${results.length} succeeded)`,
        timestamp: new Date(),
        agentic: {
          step: ev.step,
          maxSteps: total,
          toolCalls: ev.toolCalls || [],
          toolResults: results,
          hormones: {},
          durationMs: ev.durationMs || 0,
        },
      } as ChatMessage;
    });
  }

  /** Get a human-readable label for a tool name. */
  toolLabel(name: string): string {
    const labels: Record<string, string> = {
      browser: 'Browser',
      terminal: 'Terminal',
      bash: 'Terminal',
      read: 'Read File',
      write: 'Write File',
      edit: 'Edit File',
      file_read: 'Read File',
      file_write: 'Write File',
      file_edit: 'Edit File',
      file_search: 'Search Files',
      file_tree: 'File Tree',
      git: 'Git',
      web_search: 'Web Search',
      web_fetch: 'Web Fetch',
      wikipedia: 'Wikipedia',
      arxiv_search: 'Arxiv',
      note_memory: 'Notes',
      calculator: 'Calculator',
      chart_generate: 'Chart',
      code_analyze: 'Code Analysis',
      test_runner: 'Test Runner',
    };
    return labels[name] || name.replace(/_/g, ' ');
  }

  /** Get accent CSS class based on tool type. */
  toolAccentClass(name: string): string {
    if (name === 'browser') return 'tool-accent-browser';
    if (name === 'terminal' || name === 'bash' || name === 'test_runner') return 'tool-accent-terminal';
    if (name.startsWith('file_') || name === 'git' || name === 'read' || name === 'write' || name === 'edit') return 'tool-accent-file';
    if (name === 'web_search' || name === 'web_fetch' || name === 'wikipedia' || name === 'arxiv_search') return 'tool-accent-search';
    return 'tool-accent-default';
  }

  /** Get a short summary of tool arguments for display. */
  toolArgsSummary(call: AgenticToolCall): string {
    const args = call.arguments || {};
    if (call.name === 'browser') {
      const action = args['action'] || '';
      const url = args['url'] || args['ref'] || '';
      return action ? `${action}${url ? ': ' + url : ''}` : '';
    }
    if (call.name === 'terminal' || call.name === 'bash') {
      return args['command'] ? args['command'].substring(0, 80) : '';
    }
    if (call.name === 'read' || call.name.startsWith('file_')) {
      return args['path'] || '';
    }
    if (call.name === 'write' || call.name === 'edit') {
      return args['path'] || '';
    }
    if (call.name === 'git') {
      return args['command'] || '';
    }
    if (call.name === 'web_search' || call.name === 'wikipedia' || call.name === 'arxiv_search') {
      return args['query'] || '';
    }
    if (call.name === 'web_fetch') {
      const url = args['url'] || '';
      return url ? (url.length > 60 ? url.substring(0, 57) + '...' : url) : '';
    }
    const first = Object.values(args)[0];
    return first ? String(first).substring(0, 60) : '';
  }

  /** Format milliseconds to a human-readable duration. */
  formatMs(ms: number): string {
    if (ms < 1000) return `${Math.round(ms)}ms`;
    return `${(ms / 1000).toFixed(1)}s`;
  }

  /** Get color for a hormone badge. */
  hormoneColor(name: string): string {
    const colors: Record<string, string> = {
      dopamine: '#34d399',
      serotonin: '#38bdf8',
      norepinephrine: '#fbbf24',
      cortisol: '#f87171',
      oxytocin: '#a78bfa',
    };
    return colors[name] || '#9ca3af';
  }

  /** Get a short name for a hormone. */
  hormoneShort(name: string): string {
    const shorts: Record<string, string> = {
      dopamine: 'Dop',
      serotonin: 'Ser',
      norepinephrine: 'Nor',
      cortisol: 'Cor',
      oxytocin: 'Oxy',
    };
    return shorts[name] || name.substring(0, 3);
  }

  /** Return notable hormones (above 0.25) for badge display. */
  notableHormones(hormones: Record<string, number>): { name: string; value: number; color: string }[] {
    return Object.entries(hormones || {})
      .filter(([, val]) => val >= 0.25)
      .sort(([, a], [, b]) => b - a)
      .slice(0, 3)
      .map(([name, value]) => ({
        name: this.hormoneShort(name),
        value,
        color: this.hormoneColor(name),
      }));
  }

  /** Check whether the last message is an agentic iteration (used for working indicator). */
  isLastMessageAgenticIteration(): boolean {
    if (!this.messages.length) return false;
    return this.messages[this.messages.length - 1].type === ('agentic_iteration' as any);
  }

  getDownloadUrl(path: string): string {
    return this.api.getDownloadUrl(this.agentId, path);
  }

  formatFileSize(bytes: number): string {
    if (bytes < 1024) return bytes + 'B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + 'KB';
    return (bytes / (1024 * 1024)).toFixed(1) + 'MB';
  }

  @HostListener('click', ['$event'])
  onHostClick(event: MouseEvent) {
    const target = event.target as HTMLElement;

    // Copy-button inside code blocks
    if (target.classList.contains('copy-btn')) {
      const code = target.getAttribute('data-code') || '';
      navigator.clipboard.writeText(code).then(() => {
        target.textContent = 'Copied!';
        setTimeout(() => (target.textContent = 'Copy'), 1500);
      });
      return;
    }

    // Intercept http(s) link clicks inside rendered markdown → open in in-app browser
    const anchor = target.closest('a') as HTMLAnchorElement | null;
    if (anchor) {
      const href = anchor.getAttribute('href');
      if (href && /^https?:\/\//i.test(href)) {
        event.preventDefault();
        event.stopPropagation();
        this.openUrl.emit(href);
      }
    }
  }

  // ─── Skill review actions ─────────────────────────────────────

  onApproveReview(reviewId: string): void {
    if (!reviewId || this.reviewLoading[reviewId]) return;
    this.reviewLoading[reviewId] = true;
    this.ws.expectRestart();
    this.api.approveSkillReview(reviewId).subscribe({
      next: () => {
        this.reviewResolved[reviewId] = 'approved';
      },
      error: (err) => {
        this.reviewLoading[reviewId] = false;
        console.error('Failed to approve skill review:', err);
      },
    });
  }

  onRejectReview(reviewId: string): void {
    if (!reviewId || this.reviewLoading[reviewId]) return;
    this.reviewLoading[reviewId] = true;
    this.api.rejectSkillReview(reviewId).subscribe({
      next: () => {
        this.reviewResolved[reviewId] = 'rejected';
      },
      error: (err) => {
        this.reviewLoading[reviewId] = false;
        console.error('Failed to reject skill review:', err);
      },
    });
  }

  // ─── Message feedback ────────────────────────────────────────

  openFeedback(index: number, event: Event): void {
    event.stopPropagation();
    this.feedbackMessageIndex.set(index);
    this.feedbackText = '';
    this.feedbackType.set('SPECIFIC');
  }

  closeFeedback(): void {
    this.feedbackMessageIndex.set(null);
    this.feedbackText = '';
  }

  submitFeedback(): void {
    const idx = this.feedbackMessageIndex();
    if (idx === null || !this.feedbackText.trim()) return;

    const msg = this.messages[idx];
    this.feedbackSubmitting.set(true);
    this.messageFeedback.emit({
      messageIndex: idx,
      content: this.feedbackText.trim(),
      messageContent: msg.content || '',
      feedbackType: this.feedbackType(),
      channel: (msg as any).channel || '',
      sessionKey: (msg as any).sessionKey || '',
    });

    setTimeout(() => {
      this.feedbackSubmitting.set(false);
      this.closeFeedback();
    }, 300);
  }

  private scrollToBottom() {
    try {
      const el = this.scrollContainer?.nativeElement;
      if (el) el.scrollTop = el.scrollHeight;
    } catch {}
  }
}
