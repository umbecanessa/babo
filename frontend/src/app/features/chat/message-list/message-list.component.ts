import {
  Component, Input, Output, EventEmitter, ElementRef,
  ViewChild, AfterViewChecked, HostListener, signal,
  OnChanges, SimpleChanges, OnDestroy,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterModule } from '@angular/router';
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
import { TranslateModule, TranslateService } from '@ngx-translate/core';
import { ApiService } from '../../../core/services/api.service';
import { WebSocketService } from '../../../core/services/websocket.service';
import { ChatWorkbenchService } from '../../../core/services/chat-workbench.service';
import {
  collectFilePaths,
  filePathChipLabel,
  extractPathFromToolContent,
  fileDisplayName,
  isInvalidWorkspacePathToken,
} from '../../../core/services/activity-format.util';
import { AgentWorkspaceContextService } from '../../../core/services/agent-workspace-context.service';
import { WorkspaceNavService } from '../../../core/services/workspace-nav.service';
import { enrichWorkspaceRelativePath } from '../../projects/workspace/workspace-path.util';

// Re-export for template use
export type { SignalTag };

@Component({
  selector: 'app-message-list',
  standalone: true,
  imports: [CommonModule, FormsModule, MarkdownPipe, RouterModule, TranslateModule],
  templateUrl: './message-list.component.html',
  styleUrl: './message-list.component.scss',
})
export class MessageListComponent implements OnChanges, AfterViewChecked, OnDestroy {
  @Input() messages: ChatMessage[] = [];
  @Input() streamingText = '';
  @Input() streamingReasoning = '';
  @Input() awaitingResponse = false;
  @Input() agenticActive = false;
  @Input() askUserPending = false;
  @Input() budgetPromptPending = false;
  @Input() agentId = '';
  @Output() drowsyAction = new EventEmitter<{
    action: 'confirm' | 'deny';
    index: number;
  }>();
  @Output() budgetAction = new EventEmitter<{
    action: 'extend' | 'stop';
    extraIterations?: number;
    index: number;
  }>();
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
  budgetResponded = new Set<number>();
  reviewResolved: Record<string, 'approved' | 'rejected'> = {};
  reviewLoading: Record<string, boolean> = {};

  constructor(
    private api: ApiService,
    private ws: WebSocketService,
    private workbench: ChatWorkbenchService,
    private workspaceNav: WorkspaceNavService,
    private workspaceCtx: AgentWorkspaceContextService,
    private translate: TranslateService,
  ) {}

  private t(key: string, params?: Record<string, unknown>): string {
    return this.translate.instant(key, params);
  }

  writeToolStatus(msg: ChatMessage): string {
    const tp = msg.toolProgress;
    if (!tp?.done) return this.t('chat.tool.writing');
    return tp.isError ? this.t('chat.tool.failed') : this.t('chat.tool.written');
  }

  readEditToolStatus(msg: ChatMessage): string {
    const tp = msg.toolProgress;
    const isEdit = tp?.toolName === 'edit';
    if (!tp?.done) return this.t(isEdit ? 'chat.tool.editing' : 'chat.tool.reading');
    if (tp.isError) return this.t('chat.tool.failed');
    return this.t(isEdit ? 'chat.tool.edited' : 'chat.tool.read');
  }

  thoughtLabel(iteration?: number): string {
    if ((iteration ?? 0) > 1) {
      return this.t('chat.tool.thoughtStep', { step: iteration });
    }
    return this.t('chat.tool.thought');
  }

  reviewResolvedLabel(state: 'approved' | 'rejected'): string {
    return state === 'approved'
      ? this.t('chat.tool.approvedDetail')
      : this.t('chat.tool.rejectedDetail');
  }

  waitToolLabel(msg: ChatMessage): string {
    const seconds = msg.toolProgress?.arguments?.['seconds'] ?? '';
    return msg.toolProgress?.done
      ? this.t('chat.tool.waited', { seconds })
      : this.t('chat.tool.waiting', { seconds });
  }

  toolFilePaths(msg: ChatMessage): string[] {
    const tp = (msg as any).toolProgress as {
      toolName?: string;
      arguments?: Record<string, unknown>;
      filePaths?: string[];
    } | undefined;
    if (!tp?.toolName) {
      return [];
    }
    if (tp.filePaths?.length) {
      const pd = this.workspaceCtx.getProjectDir(this.agentId);
      return pd
        ? tp.filePaths.map((p) => enrichWorkspaceRelativePath(p, pd))
        : tp.filePaths;
    }
    const fromArgs = collectFilePaths(
      tp.toolName,
      tp.arguments,
      undefined,
      (msg as any).content || '',
    );
    if (fromArgs.length) {
      const pd = this.workspaceCtx.getProjectDir(this.agentId);
      return pd
        ? fromArgs.map((p) => enrichWorkspaceRelativePath(p, pd))
        : fromArgs;
    }
    const fromContent = extractPathFromToolContent((msg as any).content || '');
    return fromContent ? [fromContent] : [];
  }

  fileChipLabel(path: string): string {
    return filePathChipLabel(path, this.workspaceCtx.getProjectDir(this.agentId));
  }

  openToolFile(path: string, event: Event): void {
    event.preventDefault();
    event.stopPropagation();
    const cleaned = (path || '').trim();
    if (!this.agentId || !cleaned || isInvalidWorkspacePathToken(cleaned)) {
      return;
    }
    this.workspaceNav.openFile(this.agentId, cleaned);
  }

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
  /** When false, user scrolled up — do not follow streaming/thinking updates. */
  private scrollPinnedToBottom = true;
  private static readonly SCROLL_PIN_THRESHOLD_PX = 80;

  collapsedWaveGroups = new Set<string>();
  private _autoCollapsedWaves = new Set<string>();

  private static readonly _HIGHLIGHT_TOOLS = new Set([
    'write', 'edit', 'bash', 'delete_file', 'move_file', 'task_complete',
    'web_search', 'web_fetch', 'server_install', 'semantic_search',
  ]);

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['awaitingResponse']?.currentValue === true) {
      this.scrollPinnedToBottom = true;
    }

    if (
      changes['messages'] ||
      changes['streamingText'] ||
      changes['streamingReasoning'] ||
      changes['awaitingResponse'] ||
      changes['agenticActive'] ||
      changes['askUserPending']
    ) {
      if (this.scrollPinnedToBottom) {
        this.scrollToBottomAfterView = true;
      }
      if (changes['messages']) {
        this._syncWaveAutoCollapse();
      }
    }
    this._updateGenerationTimer();
  }

  isInGroupedWave(messageIndex: number): boolean {
    const msg = this.messages[messageIndex];
    if (msg?.type !== 'delegate_card' || !msg.delegate?.teamId) return false;
    return !this.isFirstInWave(messageIndex);
  }

  isFirstInWave(messageIndex: number): boolean {
    const msg = this.messages[messageIndex];
    if (msg?.type !== 'delegate_card' || !msg.delegate?.teamId) return true;
    const tid = msg.delegate.teamId;
    for (let j = messageIndex - 1; j >= 0; j--) {
      const prev = this.messages[j];
      if (prev?.type === 'delegate_card' && prev.delegate?.teamId === tid) {
        return false;
      }
      if (prev?.type !== 'delegate_card') break;
    }
    return true;
  }

  getWaveMemberIndices(startIndex: number): number[] {
    const msg = this.messages[startIndex];
    const tid = msg?.delegate?.teamId;
    if (!tid) return [startIndex];
    const indices: number[] = [];
    for (let j = startIndex; j < this.messages.length; j++) {
      const m = this.messages[j];
      if (m?.type !== 'delegate_card' || m.delegate?.teamId !== tid) break;
      indices.push(j);
    }
    return indices;
  }

  waveKey(teamId: string, waveAttempt?: number): string {
    return `${teamId}:${waveAttempt || 1}`;
  }

  waveLabel(msg: ChatMessage): string {
    const name = msg.delegate?.teamName || '';
    const match = name.match(/^Wave\s+\d+/i);
    if (match) return match[0];
    if (name) return name.split(' - ')[0] || name;
    return msg.delegate?.teamId?.slice(0, 8) || 'Wave';
  }

  waveSubtitle(memberIndices: number[]): string {
    const members = memberIndices.map(i => this.messages[i].delegate!);
    const done = members.filter(d => d.status === 'done').length;
    const running = members.filter(d => d.status === 'running').length;
    const failed = members.filter(d => d.status === 'error').length;
    if (running > 0) return `${done}/${members.length} done · ${running} working`;
    if (failed > 0) return `${done}/${members.length} done · ${failed} failed`;
    return `${done}/${members.length} complete`;
  }

  waveAnyRunning(memberIndices: number[]): boolean {
    return memberIndices.some(i => this.messages[i].delegate?.status === 'running');
  }

  waveAllDone(memberIndices: number[]): boolean {
    return memberIndices.every(i => {
      const s = this.messages[i].delegate?.status;
      return s === 'done' || s === 'error';
    });
  }

  isWaveCollapsed(teamId: string, waveAttempt?: number): boolean {
    return this.collapsedWaveGroups.has(this.waveKey(teamId, waveAttempt));
  }

  toggleWaveGroup(teamId: string, waveAttempt?: number): void {
    const key = this.waveKey(teamId, waveAttempt);
    if (this.collapsedWaveGroups.has(key)) {
      this.collapsedWaveGroups.delete(key);
    } else {
      this.collapsedWaveGroups.add(key);
    }
  }

  visibleDelegateTools(delegate: NonNullable<ChatMessage['delegate']>): typeof delegate.toolCalls {
    const all = delegate.toolCalls || [];
    const interesting = all.filter(tc => MessageListComponent._HIGHLIGHT_TOOLS.has(tc.name));
    return interesting.length > 0 ? interesting : all.slice(-5);
  }

  hiddenDelegateToolCount(delegate: NonNullable<ChatMessage['delegate']>): number {
    const all = delegate.toolCalls || [];
    const shown = this.visibleDelegateTools(delegate);
    return Math.max(0, all.length - shown.length);
  }

  private _syncWaveAutoCollapse(): void {
    const seen = new Set<string>();
    for (let i = 0; i < this.messages.length; i++) {
      if (!this.isFirstInWave(i)) continue;
      const msg = this.messages[i];
      if (msg?.type !== 'delegate_card' || !msg.delegate?.teamId) continue;
      const indices = this.getWaveMemberIndices(i);
      if (indices.length < 2) continue;
      const key = this.waveKey(msg.delegate.teamId, msg.delegate.waveAttempt);
      if (seen.has(key)) continue;
      seen.add(key);
      if (this._autoCollapsedWaves.has(key)) continue;
      if (this.waveAllDone(indices) && !this.waveAnyRunning(indices)) {
        this._autoCollapsedWaves.add(key);
        this.collapsedWaveGroups.add(key);
      }
    }
  }

  ngOnDestroy(): void {
    this._clearGenTimer();
  }

  ngAfterViewChecked(): void {
    if (!this.scrollToBottomAfterView) return;
    this.scrollToBottomAfterView = false;
    this.followScrollIfPinned();
  }

  /** True when the agentic planning indicator should display. */
  get showGenerationIndicator(): boolean {
    return this.agenticActive
      && !this.askUserPending
      && !this.budgetPromptPending
      && !this.streamingText?.trim()
      && !this.streamingReasoning?.trim()
      && this.isLastMessageAgenticIteration();
  }

  /** True while ask_user is blocking the loop. */
  get showAskUserIndicator(): boolean {
    return this.agenticActive && this.askUserPending;
  }

  /** True while a budget extension prompt is blocking the loop. */
  get showBudgetPromptIndicator(): boolean {
    return this.agenticActive && this.budgetPromptPending;
  }

  /** True after the user sends until tokens or a reply arrive. */
  get showAwaitingIndicator(): boolean {
    return this.awaitingResponse
      && !this.streamingText?.trim()
      && !this.streamingReasoning?.trim()
      && !this.showGenerationIndicator;
  }

  get generationLabel(): string {
    const s = this.generationElapsed();
    if (s < 10) return this.t('chat.tool.planning');
    const elapsed = s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
    return this.t('chat.tool.crunching', { elapsed });
  }

  get awaitingLabel(): string {
    const s = this.generationElapsed();
    if (s < 10) return this.t('chat.tool.thinking');
    const elapsed = s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
    return this.t('chat.tool.stillThinking', { elapsed });
  }

  /** True when any in-flight indicator should display. */
  get showPendingIndicator(): boolean {
    return this.showAskUserIndicator
      || this.showBudgetPromptIndicator
      || this.showAwaitingIndicator
      || this.showGenerationIndicator;
  }

  get pendingLabel(): string {
    if (this.showBudgetPromptIndicator) {
      return this.t('chat.tool.waitDecision');
    }
    if (this.showAskUserIndicator) {
      return this.t('chat.tool.waitAnswer');
    }
    if (this.showGenerationIndicator) {
      return this.generationLabel;
    }
    return this.awaitingLabel;
  }

  private _updateGenerationTimer(): void {
    const shouldRun = this.showPendingIndicator;
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
    this.drowsyAction.emit({ action, index });
  }

  /** Handle budget extension prompt button click. */
  onBudgetAction(index: number, action: 'extend' | 'stop', extraIterations?: number): void {
    this.budgetAction.emit({ action, extraIterations, index });
  }

  markBudgetResponded(index: number): void {
    this.budgetResponded.add(index);
  }

  clearBudgetResponded(index: number): void {
    this.budgetResponded.delete(index);
  }

  isBudgetResponded(index: number): boolean {
    return this.budgetResponded.has(index);
  }

  /** Mark a drowsy card responded after the server accepted the command. */
  markDrowsyResponded(index: number): void {
    this.drowsyResponded.add(index);
  }

  /** Allow retry if the sleep command failed to send or apply. */
  clearDrowsyResponded(index: number): void {
    this.drowsyResponded.delete(index);
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
      dopamine: 'var(--accent-success)',
      serotonin: 'var(--accent-primary)',
      norepinephrine: 'var(--accent-warn)',
      cortisol: 'var(--accent-danger)',
      oxytocin: 'var(--accent-primary)',
    };
    return colors[name] || 'var(--text-muted)';
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

  onScrollContainerScroll(event: Event): void {
    const el = event.currentTarget as HTMLElement;
    this.scrollPinnedToBottom = this._isNearBottom(el);
  }

  /** Unpin immediately on upward wheel so streaming updates cannot win the race. */
  onScrollContainerWheel(event: WheelEvent): void {
    if (event.deltaY >= 0) return;
    const el = this.scrollContainer?.nativeElement;
    if (!el || el.scrollTop <= 0) return;
    this.scrollPinnedToBottom = false;
  }

  private followScrollIfPinned(): void {
    const el = this.scrollContainer?.nativeElement;
    if (!el || !this.scrollPinnedToBottom) return;
    if (!this._isNearBottom(el)) {
      this.scrollPinnedToBottom = false;
      return;
    }
    el.scrollTop = el.scrollHeight;
  }

  private scrollToBottom() {
    this.scrollPinnedToBottom = true;
    this.followScrollIfPinned();
  }

  private _isNearBottom(el: HTMLElement): boolean {
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    return distance <= MessageListComponent.SCROLL_PIN_THRESHOLD_PX;
  }
}
