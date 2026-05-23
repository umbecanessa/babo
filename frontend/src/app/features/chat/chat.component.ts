import { Component, OnInit, OnDestroy, signal, computed, ViewChild, ElementRef, AfterViewChecked } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { ActivatedRoute } from '@angular/router';
import { Subscription } from 'rxjs';
import { WebSocketService, ChatMessage } from '../../core/services/websocket.service';
import { ChatUiSnapshotService } from '../../core/services/chat-ui-snapshot.service';
import { ChatWorkbenchService } from '../../core/services/chat-workbench.service';
import { ApiService, FileAttachment } from '../../core/services/api.service';
import { PlatformService } from '../../core/services/platform.service';
import { VoiceRecorderService } from '../../core/services/voice-recorder.service';
import { ToastService } from '../../shared/toast/toast.service';
import { parseTags, parseThinking } from '../../shared/signal-utils';
import { Agent } from '../../core/models/agent.model';
import { MessageListComponent } from './message-list/message-list.component';
import { SignalSidebarComponent, ActivityKind } from './signal-sidebar/signal-sidebar.component';
import { AgentBrowserComponent } from './agent-browser/agent-browser.component';
import { OnboardingModalComponent } from '../../shared/onboarding/onboarding-modal.component';
import { GoogleConnectModalComponent } from '../../shared/google-connect-modal/google-connect-modal.component';
import { ChatWorkbenchComponent } from './chat-workbench/chat-workbench.component';
import { ONBOARDING_PAGES } from '../../shared/onboarding/onboarding-content';

@Component({
  selector: 'app-chat',
  standalone: true,
  imports: [CommonModule, FormsModule, MessageListComponent, SignalSidebarComponent, AgentBrowserComponent, OnboardingModalComponent, GoogleConnectModalComponent, ChatWorkbenchComponent],
  templateUrl: './chat.component.html',
  styleUrl: './chat.component.scss',
})
export class ChatComponent implements OnInit, OnDestroy, AfterViewChecked {
  @ViewChild('messageInput') messageInput!: ElementRef<HTMLTextAreaElement>;
  @ViewChild('waveformCanvas') waveformCanvas!: ElementRef<HTMLCanvasElement>;
  @ViewChild('fileInput') fileInput!: ElementRef<HTMLInputElement>;
  @ViewChild(SignalSidebarComponent) signalSidebar?: SignalSidebarComponent;
  @ViewChild('onboardingModal') onboardingModal?: OnboardingModalComponent;

  agent = signal<Agent | null>(null);
  agentOnline = signal(true);
  onboardingConfig = ONBOARDING_PAGES['chat'];
  messages = signal<ChatMessage[]>([]);
  inputText = '';
  sidebarOpen = signal(true);
  streamingText = signal('');
  streamingReasoning = signal('');
  nlsMetadata = signal<any>(null);
  latestProbeSignals = signal<{
    signals: Record<string, number>;
    fired: string[];
    iteration: number;
    midGeneration: boolean;
    ts: Date;
  } | null>(null);
  _toolCallArgsAcc: Record<number, { name: string; raw: string }> = {};
  private _preToolReasoning = '';
  private _iterTextCommitted = false;
  private _pendingIterText = '';  // text generated alongside tool calls, saved before streamingText is cleared
  private _activePlanStepIdx = -1;
  private _planBackendDriven = false;
  private _planStepPrevStatus: ('pending' | 'active' | 'done' | 'error')[] = [];

  // Floating plan checklist
  planSteps = signal<{ label: string; status: 'pending' | 'active' | 'done' | 'error'; detail?: string }[]>([]);
  planExpanded = signal(false);

  bottomSheetOpen = signal(false);
  daydreams = signal<any[]>([]);
  activities = signal<any[]>([]);

  // Agentic loop state
  agenticActive = signal(false);
  agenticStep = signal(0);
  agenticMaxSteps = signal(0);
  activityStatus = signal('');
  agenticStopping = signal(false);
  lastAgenticResult = signal<{ steps: number; tools: number; durationMs: number; aborted: boolean } | null>(null);
  backgroundTaskActive = signal(false);
  private _backgroundTaskId: number = 0;
  private _bgPlanSteps: string[] = [];
  private _bgPlanStatuses: string[] = [];

  // Active delegate numbers (presence check only — index looked up dynamically)
  private _activeDelegates: Set<number> = new Set();
  // Buffer for sub-agent events that arrive before the delegate card is created
  private _delegateEventBuffer: Map<number, any[]> = new Map();

  /** Find the current index of a delegate card by its number (stable across splices). */
  private _findDelegateIdx(msgs: readonly ChatMessage[], dlgNum: number): number {
    for (let i = msgs.length - 1; i >= 0; i--) {
      if ((msgs[i] as any).delegate?.number === dlgNum) return i;
    }
    return -1;
  }

  // Ask-user state (agent waiting for human input)
  askUserPending = signal(false);

  // Google Workspace connect modal (triggered by agent or UI)
  googleModalOpen = signal(false);

  // In-app browser state
  browserExpanded = signal(false);
  browserCommand = signal<any>(null);

  // File attachment state
  pendingAttachments = signal<FileAttachment[]>([]);
  isDragOver = signal(false);
  fileUploading = signal(false);

  // Thread switcher state
  currentThread = signal<string>('websocket:main');
  activeThreads = signal<{ key: string; label: string; channel: string; sender?: string; subject?: string }[]>([
    { key: 'websocket:main', label: 'Main Chat', channel: 'websocket' },
  ]);
  threadDropdownOpen = signal(false);

  /** Threads grouped by channel for the dropdown UI */
  groupedThreads = computed(() => {
    const threads = this.activeThreads();
    const groups: { channel: string; label: string; icon: string; threads: typeof threads }[] = [];
    const channelOrder = ['websocket', 'email', 'telegram', 'whatsapp'];
    const channelLabels: Record<string, string> = {
      websocket: 'Direct',
      email: 'Email',
      telegram: 'Telegram',
      whatsapp: 'WhatsApp',
    };
    const channelIcons: Record<string, string> = {
      websocket: 'chat',
      email: 'email',
      telegram: 'telegram',
      whatsapp: 'whatsapp',
    };

    for (const ch of channelOrder) {
      const chThreads = threads.filter(t => t.channel === ch);
      if (chThreads.length > 0) {
        groups.push({
          channel: ch,
          label: channelLabels[ch] || ch,
          icon: channelIcons[ch] || 'chat',
          threads: chThreads,
        });
      }
    }
    // Any channels not in the predefined order
    const known = new Set(channelOrder);
    const extra = threads.filter(t => !known.has(t.channel));
    if (extra.length > 0) {
      const byChannel = new Map<string, typeof threads>();
      for (const t of extra) {
        const arr = byChannel.get(t.channel) || [];
        arr.push(t);
        byChannel.set(t.channel, arr);
      }
      for (const [ch, chThreads] of byChannel) {
        groups.push({ channel: ch, label: ch, icon: 'chat', threads: chThreads });
      }
    }
    return groups;
  });

  /** Messages filtered by the currently selected thread */
  filteredMessages = computed(() => {
    const thread = this.currentThread();
    const msgs = this.messages();
    if (thread === 'websocket:main') {
      return msgs.filter(m => !m.sessionKey || m.sessionKey === 'websocket:main');
    }
    return msgs.filter(m => m.sessionKey === thread);
  });

  /** Active thread metadata for context banners */
  activeThreadMeta = computed(() => {
    const key = this.currentThread();
    return this.activeThreads().find(t => t.key === key) || null;
  });

  /** Connected channel names for the sidebar status strip */
  connectedChannels = computed(() => {
    const threads = this.activeThreads();
    const channels = new Set(threads.map(t => t.channel).filter(c => c !== 'websocket'));
    return Array.from(channels);
  });

  // ANS safety net: buffer learnings during agentic tasks
  // so they attach to the completion message, not pre-task chat.
  private _bufferedLearnings: string[] = [];

  private _agenticStepEvents: { step: number; toolCalls: { name: string }[]; toolResults: { success: boolean }[]; durationMs: number }[] = [];

  private sub!: Subscription;
  agentId = '';
  private waveformAnimFrame = 0;

  constructor(
    private route: ActivatedRoute,
    public ws: WebSocketService,
    public api: ApiService,
    public platform: PlatformService,
    public voice: VoiceRecorderService,
    private toast: ToastService,
    private http: HttpClient,
    private chatUiSnapshot: ChatUiSnapshotService,
    readonly workbench: ChatWorkbenchService,
  ) {}

  ngOnInit() {
    this.agentId = this.route.snapshot.params['agentId'];
    this.workbench.bindAgent(this.agentId);
    this.restoreChatUiSnapshot();

    // Load agent info
    this.api.getAgent(this.agentId).subscribe({
      next: (agent) => this.agent.set(agent),
    });

    // In remote mode, check relay before connecting
    if (this.platform.isRemote) {
      this.api.getRelayStatus(this.agentId).subscribe({
        next: (status) => {
          this.agentOnline.set(status.online);
          if (status.online) {
            this.ws.connect();
            this.ws.joinAgent(this.agentId);
            this.loadPersistedThreads();
          }
        },
        error: () => this.agentOnline.set(false),
      });
    } else {
      this.ws.connect();
      this.ws.joinAgent(this.agentId);
      this.loadPersistedThreads();
    }

    // Restore background task activity card if one was running before navigation
    this._restoreBackgroundState();

    // Listen for messages
    this.sub = this.ws.onMessage(this.agentId).subscribe((msg) => {
      this.handleRuntimeMessage(msg);
    });

    // Prefill from query param (used by skill escalation)
    const prefill = this.route.snapshot.queryParams['prefill'];
    if (prefill) {
      this.inputText = prefill;
      setTimeout(() => this.messageInput?.nativeElement?.focus(), 200);
    }

    // Skill setup from query param (used by settings channel cards)
    const setupSkill = this.route.snapshot.queryParams['setup'];
    if (setupSkill) {
      if (setupSkill === 'google-workspace') {
        this.googleModalOpen.set(true);
      } else {
        this.triggerSkillSetup(setupSkill);
      }
    }
  }

  private triggerSkillSetup(skillName: string): void {
    const friendly = skillName.replace('-channel', '').replace('-', ' ');

    this.messages.update(msgs => [...msgs, {
      type: 'status',
      content: `Starting ${friendly} setup...`,
      timestamp: new Date(),
    }]);

    const send = () => this.ws.send({
      type: 'message',
      content: `I want to connect ${friendly}`,
      skill_setup: skillName,
    });

    // In Electron / raw-WS mode the connection is established
    // asynchronously after joinAgent().  Wait for the "Connected"
    // status message before sending so the message isn't dropped.
    const sub = this.ws.onMessage().subscribe(msg => {
      if (msg.type === 'status' && (msg.content || '').includes('Connected')) {
        sub.unsubscribe();
        setTimeout(send, 150);
      }
    });
    // Safety: if already connected (Socket.IO mode), send immediately
    setTimeout(() => {
      if (!sub.closed) {
        sub.unsubscribe();
        send();
      }
    }, 3000);
  }

  ngOnDestroy() {
    this.persistChatUiSnapshot();
    this.sub?.unsubscribe();
    // Do not disconnect: shared WebSocket stays open for the agent across
    // Chat / Tasks / IDE so agentic runs are not cancelled on route change.
  }

  /** Restore transcript + Brain panel state after navigating back to Chat. */
  private restoreChatUiSnapshot(): void {
    if (!this.agentId) return;
    const snap = this.chatUiSnapshot.take(this.agentId);
    if (!snap) return;
    this.messages.set(snap.messages ?? []);
    if (snap.nlsMetadata != null) {
      this.nlsMetadata.set(snap.nlsMetadata);
    }
    this.activities.set(snap.activities ?? []);
    this.daydreams.set(snap.daydreams ?? []);
    this.planSteps.set(snap.planSteps ?? []);
    this.planExpanded.set(snap.planExpanded ?? false);
    if (snap.latestProbeSignals != null) {
      this.latestProbeSignals.set(snap.latestProbeSignals);
    }
    this.agenticActive.set(snap.agenticActive ?? false);
    this.agenticStep.set(snap.agenticStep ?? 0);
    this.agenticMaxSteps.set(snap.agenticMaxSteps ?? 0);
    this.activityStatus.set(snap.activityStatus ?? '');
    this.agenticStopping.set(snap.agenticStopping ?? false);
    this.backgroundTaskActive.set(snap.backgroundTaskActive ?? false);
    this.workbench.restoreState(
      snap.workbenchOpen ?? false,
      snap.workbenchEntries ?? [],
    );
  }

  /** Merge heartbeat snapshots so partial WS payloads do not wipe BPM or digests. */
  private _mergeHeartbeat(
    prev: Record<string, unknown> | undefined | null,
    incoming: Record<string, unknown> | undefined | null,
  ): Record<string, unknown> {
    const p = prev && typeof prev === 'object' ? { ...prev } : {};
    const i = incoming && typeof incoming === 'object' ? incoming : null;
    if (!i || Object.keys(i).length === 0) return p;
    return { ...p, ...i };
  }

  private persistChatUiSnapshot(): void {
    if (!this.agentId) return;
    this.chatUiSnapshot.save(this.agentId, {
      messages: this.messages(),
      nlsMetadata: this.nlsMetadata(),
      activities: this.activities(),
      daydreams: this.daydreams(),
      planSteps: this.planSteps(),
      planExpanded: this.planExpanded(),
      latestProbeSignals: this.latestProbeSignals(),
      agenticActive: this.agenticActive(),
      agenticStep: this.agenticStep(),
      agenticMaxSteps: this.agenticMaxSteps(),
      activityStatus: this.activityStatus(),
      agenticStopping: this.agenticStopping(),
      backgroundTaskActive: this.backgroundTaskActive(),
      workbenchOpen: this.workbench.panelOpen(),
      workbenchEntries: this.workbench.snapshotState().entries,
    });
  }

  sendMessage() {
    const text = this.inputText.trim();
    const attachments = this.pendingAttachments();
    if (!text && attachments.length === 0) return;

    // Check for slash commands (only when no attachments)
    if (text.startsWith('/') && attachments.length === 0) {
      this.handleSlashCommand(text);
      this.inputText = '';
      return;
    }

    const displayParts: string[] = [];
    if (text) displayParts.push(text);

    const threadKey = this.currentThread();

    // Add user message with attachments stored separately (rendered as chips)
    this.messages.update(msgs => [...msgs, {
      type: 'user',
      content: displayParts.join('\n'),
      attachments: attachments.length > 0
        ? attachments.map(a => ({ name: a.name, path: a.path, mime_type: a.mime_type, size: a.size }))
        : undefined,
      timestamp: new Date(),
      sessionKey: threadKey !== 'websocket:main' ? threadKey : undefined,
    }]);

    // If the agent is waiting for an answer, route as user_answer
    if (this.askUserPending()) {
      this.ws.send({ type: 'user_answer', content: text, session_key: threadKey });
      this.askUserPending.set(false);
    } else {
      if (attachments.length > 0) {
        this.ws.send({
          type: 'message',
          content: text || 'Please examine the attached files.',
          attachments,
          session_key: threadKey,
        });
      } else {
        this.ws.sendMessage(text, threadKey);
      }
    }

    this.inputText = '';
    this.pendingAttachments.set([]);
    if (!this.agenticActive()) {
      this.streamingText.set('');
      this.streamingReasoning.set('');
    }
    setTimeout(() => this.resetTextareaHeight(), 0);
  }

  private handleSlashCommand(cmd: string) {
    const lower = cmd.toLowerCase().trim();

    if (lower === '/sleep') {
      this.ws.sendCommand('sleep');
      this.messages.update(msgs => [...msgs, {
        type: 'status',
        content: 'Requesting sleep cycle...',
        timestamp: new Date(),
      }]);
    } else if (lower === '/status') {
      this.ws.sendCommand('status', {
        sections: ['hormones', 'ans', 'heartbeat', 'working_memory', 'narrative', 'theory_of_mind', 'predictive_processing', 'network_dynamics'],
      });
    } else if (lower === '/abort' || lower === '/stop') {
      this.cancelAgentic();
    } else if (lower === '/help') {
      this.messages.update(msgs => [...msgs, {
        type: 'status',
        content: 'Commands: /sleep (trigger sleep cycle), /status (refresh status), /abort (stop agentic loop), /help',
        timestamp: new Date(),
      }]);
    } else {
      this.messages.update(msgs => [...msgs, {
        type: 'status',
        content: `Unknown command: ${cmd}. Type /help for available commands.`,
        timestamp: new Date(),
      }]);
    }
  }

  onKeyDown(event: KeyboardEvent) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      this.sendMessage();
    }
  }

  adjustTextareaHeight(): void {
    const el = this.messageInput?.nativeElement;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 200) + 'px';
  }

  private resetTextareaHeight(): void {
    const el = this.messageInput?.nativeElement;
    if (!el) return;
    el.style.height = 'auto';
  }

  // ─── File Handling ───────────────────────────────────────────
  onFilesSelected(event: Event) {
    const input = event.target as HTMLInputElement;
    if (input.files && input.files.length > 0) {
      this.uploadFiles(Array.from(input.files));
    }
    input.value = '';
  }

  onDragOver(event: DragEvent) {
    event.preventDefault();
    event.stopPropagation();
    this.isDragOver.set(true);
  }

  onDragLeave(event: DragEvent) {
    event.preventDefault();
    event.stopPropagation();
    this.isDragOver.set(false);
  }

  onDrop(event: DragEvent) {
    event.preventDefault();
    event.stopPropagation();
    this.isDragOver.set(false);

    const files = event.dataTransfer?.files;
    if (files && files.length > 0) {
      this.uploadFiles(Array.from(files));
    }
  }

  removeAttachment(path: string) {
    this.pendingAttachments.update(list => list.filter(a => a.path !== path));
  }

  private uploadFiles(files: File[]) {
    if (!this.agentId || files.length === 0) return;
    this.fileUploading.set(true);

    this.api.uploadFiles(this.agentId, files).subscribe({
      next: (uploaded) => {
        this.pendingAttachments.update(list => [...list, ...uploaded]);
        this.fileUploading.set(false);
      },
      error: (err) => {
        console.error('File upload failed:', err);
        this.toast.show('File upload failed', 'error');
        this.fileUploading.set(false);
      },
    });
  }

  onDrowsyAction(action: 'confirm' | 'deny') {
    if (action === 'confirm') {
      this.ws.sendCommand('sleep_confirm');
      this.messages.update(msgs => [...msgs, {
        type: 'status',
        content: 'You said: "Rest up." Agent is going to sleep...',
        timestamp: new Date(),
      }]);
    } else {
      this.ws.sendCommand('sleep_deny');
      this.messages.update(msgs => [...msgs, {
        type: 'status',
        content: 'You said: "Stay awake." Agent will keep going.',
        timestamp: new Date(),
      }]);
    }
  }

  ngAfterViewChecked() {
    // Draw waveform when recording
    if (this.voice.state() === 'recording' && this.waveformCanvas) {
      this._drawWaveform();
    }
  }

  toggleSidebar() {
    this.sidebarOpen.update(v => !v);
  }

  formatMs(ms: number): string {
    if (!ms || ms <= 0) return '';
    if (ms < 1000) return `${Math.round(ms)}ms`;
    return `${(ms / 1000).toFixed(1)}s`;
  }

  openBottomSheet() {
    this.bottomSheetOpen.set(true);
  }

  closeBottomSheet() {
    this.bottomSheetOpen.set(false);
  }

  /** Top hormone name + value for the condensed mobile bar. */
  get topHormone(): { name: string; value: number; color: string } | null {
    const h = this.nlsMetadata()?.hormones;
    if (!h) return null;
    const map: Record<string, { name: string; color: string }> = {
      dopamine: { name: 'Dop', color: '#34d399' },
      serotonin: { name: 'Ser', color: '#38bdf8' },
      norepinephrine: { name: 'Nor', color: '#fbbf24' },
      cortisol: { name: 'Cor', color: '#f87171' },
      oxytocin: { name: 'Oxy', color: '#a78bfa' },
    };
    let top: { name: string; value: number; color: string } | null = null;
    for (const [key, meta] of Object.entries(map)) {
      const val = h[key] || 0;
      if (!top || val > top.value) {
        top = { name: meta.name, value: val, color: meta.color };
      }
    }
    return top && top.value > 0 ? top : null;
  }

  get condensedAns(): string {
    const state = this.nlsMetadata()?.ans?.state || '';
    return state ? state.charAt(0).toUpperCase() + state.slice(1) : '';
  }

  get condensedFacts(): number {
    return this.nlsMetadata()?.facts_in_memory || 0;
  }

  async startVoice() {
    try {
      await this.voice.startRecording();
      this._startWaveformLoop();
    } catch (err) {
      console.error('Microphone access denied:', err);
    }
  }

  async stopVoice() {
    try {
      const blob = await this.voice.stopRecording();
      cancelAnimationFrame(this.waveformAnimFrame);

      // Send to backend for transcription
      this.api.transcribe(blob).subscribe({
        next: (result) => {
          this.inputText = result.text;
          this.voice.finishTranscribing();
          // Focus the textarea so user can edit/send
          setTimeout(() => this.messageInput?.nativeElement?.focus(), 50);
        },
        error: (err) => {
          console.error('Transcription failed:', err);
          this.voice.finishTranscribing();
        },
      });
    } catch (err) {
      console.error('Stop recording failed:', err);
      this.voice.cancelRecording();
    }
  }

  cancelVoice() {
    cancelAnimationFrame(this.waveformAnimFrame);
    this.voice.cancelRecording();
  }

  cancelAgentic(): void {
    if (!this.agenticActive()) {
      this.messages.update(msgs => [...msgs, {
        type: 'status',
        content: 'No agentic task running.',
        timestamp: new Date(),
      }]);
      return;
    }
    this.agenticStopping.set(true);
    this.ws.sendAbort();
    this.messages.update(msgs => [...msgs, {
      type: 'status',
      content: 'Stopping agent task...',
      timestamp: new Date(),
    }]);
  }

  /** Format seconds as m:ss */
  formatDuration(secs: number): string {
    const m = Math.floor(secs / 60);
    const s = secs % 60;
    return `${m}:${s.toString().padStart(2, '0')}`;
  }

  private _startWaveformLoop() {
    const draw = () => {
      if (this.voice.state() !== 'recording') return;
      this._drawWaveform();
      this.waveformAnimFrame = requestAnimationFrame(draw);
    };
    this.waveformAnimFrame = requestAnimationFrame(draw);
  }

  private _drawWaveform() {
    const canvas = this.waveformCanvas?.nativeElement;
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const data = this.voice.waveformData();
    if (!data || data.length === 0) return;

    const width = canvas.width;
    const height = canvas.height;
    ctx.clearRect(0, 0, width, height);

    // Draw waveform
    ctx.lineWidth = 2;
    ctx.strokeStyle = '#38bdf8';
    ctx.beginPath();

    const sliceWidth = width / data.length;
    let x = 0;

    for (let i = 0; i < data.length; i++) {
      const v = data[i] / 128.0;
      const y = (v * height) / 2;
      if (i === 0) {
        ctx.moveTo(x, y);
      } else {
        ctx.lineTo(x, y);
      }
      x += sliceWidth;
    }

    ctx.lineTo(width, height / 2);
    ctx.stroke();
  }

  /**
   * Update the background activity card's detail text, creating it if needed.
   */
  private _updateBackgroundCardDetail(detail: string): void {
    if (!this._backgroundTaskId || !this.activities().some(a => a.id === this._backgroundTaskId)) {
      this.backgroundTaskActive.set(true);
      this._backgroundTaskId = Date.now();
      this.activities.update(acts => [{
        id: this._backgroundTaskId, kind: 'todo' as any,
        text: 'Working on background task...',
        detail,
        tags: [], signals: 0, factsStored: 0, timestamp: new Date(),
        metadata: { autonomous: true, step: 0, maxSteps: 15, source: 'system' },
      }, ...acts].slice(0, 20));
    } else {
      this.activities.update(acts => acts.map(a =>
        a.id === this._backgroundTaskId ? { ...a, detail } : a
      ));
    }
    this._persistBackgroundState();
  }

  /**
   * Add a discrete activity card for an autonomous action (tool call,
   * plan event, step completion, etc.) so the sidebar shows a live
   * stream of what the agent is doing -- not just one card that updates.
   */
  private _addAutonomousActivityCard(
    kind: 'agentic_task' | 'todo',
    text: string,
    detail?: string,
    metadata?: Record<string, any>,
  ): number {
    const id = Date.now() + Math.floor(Math.random() * 1000);
    this.activities.update(acts => [{
      id, kind: kind as any, text,
      detail: detail || '',
      tags: [], signals: 0, factsStored: 0, timestamp: new Date(),
      metadata: { autonomous: true, ...metadata },
    }, ...acts].slice(0, 30));
    return id;
  }

  /**
   * Persist background task state to sessionStorage for survival across tab switches.
   */
  private _persistBackgroundState(): void {
    if (!this.backgroundTaskActive()) {
      sessionStorage.removeItem(`bg_task_${this.agentId}`);
      return;
    }
    const card = this.activities().find(a => a.id === this._backgroundTaskId);
    if (card) {
      sessionStorage.setItem(`bg_task_${this.agentId}`, JSON.stringify({
        id: card.id,
        text: card.text,
        detail: card.detail,
        metadata: card.metadata,
        timestamp: new Date().toISOString(),
      }));
    }
  }

  /**
   * Restore background task activity card from sessionStorage on remount.
   */
  private _restoreBackgroundState(): void {
    const raw = sessionStorage.getItem(`bg_task_${this.agentId}`);
    if (!raw) return;
    try {
      const saved = JSON.parse(raw);
      if (saved.metadata?.completed) {
        sessionStorage.removeItem(`bg_task_${this.agentId}`);
        return;
      }
      const age = Date.now() - new Date(saved.timestamp).getTime();
      if (age > 10 * 60 * 1000) {
        sessionStorage.removeItem(`bg_task_${this.agentId}`);
        return;
      }
      this._backgroundTaskId = saved.id;
      this.backgroundTaskActive.set(true);
      this.activities.update(acts => [{
        id: saved.id, kind: 'todo' as any,
        text: saved.text || 'Working on background task...',
        detail: saved.detail || 'Reconnecting...',
        tags: [], signals: 0, factsStored: 0, timestamp: new Date(saved.timestamp),
        metadata: saved.metadata || { autonomous: true },
      }, ...acts].slice(0, 20));
    } catch {
      sessionStorage.removeItem(`bg_task_${this.agentId}`);
    }
  }

  /**
   * Find the best matching pending plan step for a tool call.
   */
  private _matchToolToPlanStep(toolName: string, args: any): number {
    const steps = this.planSteps();
    if (!steps.length) return -1;

    const keywords: string[] = [];
    if (args?.path) keywords.push(...args.path.split('/').pop()!.split('.'));
    if (args?.file_path) keywords.push(...args.file_path.split('/').pop()!.split('.'));
    if (args?.command) keywords.push(...args.command.split(/\s+/).slice(0, 4));
    if (args?.url) keywords.push(args.url);
    keywords.push(toolName);

    const lowerKeywords = keywords.map(k => k.toLowerCase()).filter(k => k.length > 1);

    let bestIdx = -1;
    let bestScore = 0;

    for (let i = 0; i < steps.length; i++) {
      if (steps[i].status === 'done' || steps[i].status === 'error') continue;
      const label = steps[i].label.toLowerCase();
      let score = 0;
      for (const kw of lowerKeywords) {
        if (label.includes(kw)) score += kw.length;
      }
      if (score > bestScore) {
        bestScore = score;
        bestIdx = i;
      }
    }

    if (bestIdx === -1) {
      bestIdx = steps.findIndex(s => s.status === 'pending');
    }

    return bestIdx;
  }

  private _updatePlanStep(stepIdx: number, patch: Partial<{ status: 'pending' | 'active' | 'done' | 'error'; detail: string }>) {
    if (stepIdx < 0) return;
    this.planSteps.update(steps => {
      const updated = [...steps];
      updated[stepIdx] = { ...updated[stepIdx], ...patch };
      return updated;
    });
  }

  private _appendPlanStepDetail(stepIdx: number, chunk: string) {
    if (stepIdx < 0) return;
    this.planSteps.update(steps => {
      const updated = [...steps];
      const prev = updated[stepIdx].detail || '';
      updated[stepIdx] = { ...updated[stepIdx], detail: prev + chunk };
      return updated;
    });
  }

  togglePlanExpanded(): void {
    this.planExpanded.update(v => !v);
  }

  toggleDelegateExpanded(msgIdx: number): void {
    this.messages.update(msgs => {
      if (!msgs[msgIdx]?.delegate) return msgs;
      const updated = [...msgs];
      updated[msgIdx] = {
        ...updated[msgIdx],
        delegate: {
          ...updated[msgIdx].delegate!,
          expanded: !updated[msgIdx].delegate!.expanded,
        },
      };
      return updated;
    });
  }

  get planDoneCount(): number {
    return this.planSteps().filter(s => s.status === 'done').length;
  }

  get planTotalCount(): number {
    return this.planSteps().length;
  }

  private handleRuntimeMessage(msg: any) {
    if (!msg._wbDone) {
      msg._wbDone = true;
      this.workbench.recordFromRuntime(msg);
    }
    switch (msg.type) {
      case 'history':
        // Restore conversation history from runtime on connect
        if (Array.isArray(msg.messages) && msg.messages.length > 0) {
          const restored: ChatMessage[] = [];
          for (const m of msg.messages) {
            const isAssistant = m.role !== 'user';
            const meta = m.metadata;

            // If this assistant message was part of an agentic task,
            // restore the agentic start/iteration/complete markers.
            if (isAssistant && meta?.agentic) {
              restored.push({
                type: 'agentic_start' as any,
                content: `Agent ran autonomous task (${meta.iterations || 0} steps, ${meta.tool_calls || 0} tool calls)`,
                timestamp: new Date(),
              });
              if (Array.isArray(meta.events)) {
                for (const ev of meta.events) {
                  const toolNames = (ev.tool_calls || [])
                    .map((tc: any) => tc.name || tc.tool_name || 'tool')
                    .join(', ');
                  const results = ev.tool_results || [];
                  const successes = results.filter((r: any) => r.success).length;
                  restored.push({
                    type: 'agentic_iteration' as any,
                    content: `Step ${ev.step}: ${toolNames || 'processing'} (${successes}/${results.length} succeeded)`,
                    timestamp: new Date(),
                    agentic: {
                      step: ev.step,
                      maxSteps: meta.iterations || 0,
                      toolCalls: ev.tool_calls || [],
                      toolResults: results,
                      hormones: ev.hormones || {},
                      durationMs: ev.duration_ms || 0,
                    },
                  });
                }
              }
              const restoredEvents = (meta.events || []).map((ev: any) => ({
                step: ev.step,
                toolCalls: (ev.tool_calls || []).map((tc: any) => ({ name: tc.name || 'tool' })),
                toolResults: (ev.tool_results || []).map((tr: any) => ({ success: tr.success !== false })),
                durationMs: ev.duration_ms || 0,
              }));
              restored.push({
                type: 'agentic_complete' as any,
                content: meta.aborted
                  ? `Task stopped: ${meta.abort_reason || 'Cancelled'}`
                  : 'Task complete',
                timestamp: new Date(),
                agenticComplete: {
                  totalSteps: meta.iterations || 0,
                  totalToolCalls: meta.tool_calls || 0,
                  aborted: meta.aborted || false,
                  abortReason: meta.abort_reason || '',
                  durationMs: 0,
                  hormones: {},
                  events: restoredEvents,
                },
              });
              // Restore plan steps if they were saved in metadata
              if (Array.isArray(meta.plan_steps) && meta.plan_steps.length > 0) {
                this.planSteps.set(meta.plan_steps.map((s: any) => ({
                  label: s.label || '',
                  status: (s.status === 'done' ? 'done' : s.status === 'in_progress' ? 'active' : 'pending') as any,
                })));
              }
            }

            if (isAssistant && m.content) {
              const text = m.content as string;
              if (this.isSystemInjection(text)) continue;
              const thought = parseThinking(text);
              restored.push({
                type: 'assistant' as const,
                content: thought.response || text,
                reasoning: thought.thinking || undefined,
                timestamp: new Date(),
              });
            } else {
              const rawContent = m.content || '';
              if (!rawContent.trim()) continue;
              if (this.isSystemInjection(rawContent)) continue;
              restored.push({
                type: m.role === 'user' ? 'user' as const : 'assistant' as const,
                content: rawContent,
                timestamp: new Date(),
              });
            }
          }
          this.messages.set(restored);
        }
        break;

      case 'token':
        this.streamingText.update(t => t + msg.content);
        break;

      case 'reasoning_token':
        this.streamingReasoning.update(t => t + (msg.content || ''));
        break;

      case 'reasoning_end':
        break;

      case 'response_replace':
        this.streamingText.set(msg.response || '');
        break;

      case 'response_end': {
        // Prefer msg.response (server-side cleaned/processed text) over
        // streamingText() which may contain orphan </think> artifacts and
        // duplicated pre-tag content from the raw token stream.
        const fullText = msg.response || this.streamingText() || '';
        const thought = parseThinking(fullText);
        const streamedReasoning = this.streamingReasoning();
        const reasoning = streamedReasoning || msg.reasoning || thought.thinking || '';
        const sk = msg.session_key || this.currentThread();
        // Only commit if there's actual content — prevents blank bubbles
        // from races where streamingText was cleared before response_end fires.
        if ((thought.response || fullText).trim()) {
          this.messages.update(msgs => [...msgs, {
            type: 'assistant',
            content: thought.response || fullText,
            reasoning: reasoning || undefined,
            timestamp: new Date(),
            nls: msg.nls,
            sessionKey: sk !== 'websocket:main' ? sk : undefined,
          }]);
        }
        this.streamingText.set('');
        this.streamingReasoning.set('');
      }
        if (msg.nls) {
          // Merge response NLS data with existing status data
          // (preserve sleep_count, ans, turn_count set at connect time)
          this.nlsMetadata.update(m => {
            const existing = m || {};
            const newSignals = [...(existing.signals || []), ...(msg.nls.signals || [])];
            return {
              ...existing,
              ...msg.nls,
              signals: newSignals.slice(-20),  // accumulate, keep last 20
              sleep_count: existing.sleep_count ?? 0,
              ans: existing.ans ?? {},
              turn_count: (existing.turn_count ?? 0) + 1,
            };
          });
        }
        break;

      case 'status': {
        // Only show a chat status bubble for meaningful transitions
        // (not the initial "alive" connect status)
        const statusText = msg.agent_status || msg.content || '';

        // Reset stale agentic state on reconnect/restart.
        // After a server restart the agentic_complete event is lost,
        // leaving the UI stuck in "Reasoning..." with a stale step
        // counter.  Clear it whenever we get a fresh connection status.
        if (
          this.agenticActive() &&
          (statusText === 'alive'
           || statusText.includes('Reconnected')
           || statusText.includes('restarted'))
        ) {
          this.agenticActive.set(false);
          this.agenticStopping.set(false);
          this.askUserPending.set(false);
          this.activityStatus.set('');
          this.agenticStep.set(0);
          this.streamingText.set('');
          this.streamingReasoning.set('');
          this.planExpanded.set(false);
        }

        if (statusText === 'sleeping') {
          const reason = msg.sleep_reason || 'consolidating memories';
          this.messages.update(msgs => [...msgs, {
            type: 'status',
            content: `Agent is sleeping (${reason})...`,
            timestamp: new Date(),
          }]);
        } else if (statusText === 'alive' && msg.sleep_complete) {
          this.messages.update(msgs => [...msgs, {
            type: 'status',
            content: `Agent woke up (${msg.signals_processed || 0} signals consolidated)`,
            timestamp: new Date(),
          }]);
        } else if (statusText !== 'alive') {
          // Show other status messages (errors, custom content)
          this.messages.update(msgs => [...msgs, {
            type: 'status',
            content: statusText,
            timestamp: new Date(),
          }]);
        }
        // Update agent name from initial status if available
        if (msg.agent_name) {
          this.agent.update(a => a ? { ...a, name: msg.agent_name } : a);
        }
        // Always update sidebar metadata from status messages
        this.nlsMetadata.update(m => {
          const updated: any = {
            ...m,
            facts_in_memory: msg.facts_in_memory ?? m?.facts_in_memory ?? 0,
            turn_count: msg.turn_count ?? m?.turn_count ?? 0,
            sleep_count: msg.sleep_count ?? m?.sleep_count ?? 0,
            hormones: msg.hormones ?? m?.hormones ?? {},
            ans: msg.ans ?? m?.ans ?? {},
            heartbeat: msg.heartbeat ?? m?.heartbeat ?? {},
          };
          if (msg.working_memory) updated.working_memory = msg.working_memory;
          if (msg.narrative) updated.narrative = msg.narrative;
          if (msg.theory_of_mind) updated.theory_of_mind = msg.theory_of_mind;
          if (msg.predictive_processing) updated.predictive_processing = msg.predictive_processing;
          if (msg.network_dynamics) updated.network_dynamics = msg.network_dynamics;
          return updated;
        });
        break;
      }

      case 'name_update':
        // Agent accepted a name during conversation -- update header
        if (msg.name) {
          this.agent.update(a => a ? { ...a, name: msg.name } : a);
        }
        break;

      case 'daydream': {
        const dreamParsed = parseTags(msg.content || 'Agent was daydreaming...');
        const isActive = msg.dream_type && msg.dream_type !== 'passive';
        const dreamKind = isActive ? 'active_dream' : 'dream';
        const dreamEntry = {
          id: Date.now(),
          kind: dreamKind as any,
          text: dreamParsed.text,
          tags: dreamParsed.tags,
          signals: msg.signals || 0,
          factsStored: msg.facts_stored || 0,
          timestamp: new Date(),
          detail: msg.reflection || '',
          sources: msg.sources || [],
          relevance: msg.relevance,
        };
        this.activities.update(acts => [dreamEntry, ...acts].slice(0, 10));
        this.daydreams.update(dreams => [dreamEntry, ...dreams].slice(0, 10));
        if (msg.signals || msg.facts_stored) {
          this.nlsMetadata.update(m => ({
            ...m,
            facts_in_memory: (m?.facts_in_memory || 0) + (msg.facts_stored || 0),
          }));
        }
        break;
      }

      case 'dream_finding': {
        this.activities.update(acts => [{
          id: Date.now(),
          kind: 'finding' as const,
          text: msg.research_question || msg.summary || 'Dream finding',
          tags: [],
          signals: msg.signals_extracted || 0,
          factsStored: msg.facts_stored || 0,
          timestamp: new Date(),
          detail: msg.summary || '',
          sources: msg.sources || [],
          relevance: msg.relevance,
        }, ...acts].slice(0, 10));
        break;
      }

      case 'tool_use':
        this.messages.update(msgs => [...msgs, {
          type: 'tool_use' as any,
          content: msg.query || '',
          timestamp: new Date(),
          tool: {
            name: msg.tool || 'web_search',
            query: msg.query || '',
            source: msg.source || 'web',
            preview: msg.result_preview || '',
            success: msg.success !== false,
          },
        }]);
        break;

      case 'drive_action': {
        const kind = msg.action_type === 'reach_out' ? 'reach_out' : 'drive';
        this.activities.update(acts => [{
          id: Date.now(),
          kind,
          text: msg.query || msg.result_preview || '',
          tags: [],
          signals: 0,
          factsStored: 0,
          timestamp: new Date(),
          drive: {
            name: msg.drive || '',
            actionType: msg.action_type || '',
            domain: msg.domain || '',
            query: msg.query || '',
            success: msg.success !== false,
            resultPreview: msg.result_preview || '',
          },
        }, ...acts].slice(0, 10));
        break;
      }

      case 'reach_out': {
        if (msg.content) {
          const channelLabel = msg.channel && msg.channel !== 'chat'
            ? ` via ${msg.channel}` : '';
          this.activities.update(acts => [{
            id: Date.now(),
            kind: 'reach_out' as const,
            text: msg.content,
            tags: [],
            signals: 0,
            factsStored: 0,
            timestamp: new Date(),
            metadata: {
              channel: msg.channel || 'chat',
              target: msg.target || '',
            },
          }, ...acts].slice(0, 10));
        }
        break;
      }

      case 'channel_event': {
        this.handleChannelEvent(msg);
        this.activities.update(acts => [{
          id: Date.now(),
          kind: 'channel' as const,
          text: `${msg.direction === 'inbound' ? 'Received from' : 'Sent to'} ${msg.sender || msg.channel}`,
          tags: [],
          signals: 0,
          factsStored: 0,
          timestamp: new Date(),
          metadata: {
            channel: msg.channel,
            direction: msg.direction,
            sender: msg.sender,
            contentPreview: msg.content_preview,
            sessionKey: msg.session_key,
          },
        }, ...acts].slice(0, 10));
        break;
      }

      case 'drowsy': {
        // Agent is drowsy and requesting permission to sleep.
        // Show as an amber-bordered bubble with action buttons.
        this.messages.update(msgs => [...msgs, {
          type: 'drowsy' as any,
          content: msg.content || "I'm feeling drowsy...",
          timestamp: new Date(),
          drowsy: {
            reason: msg.reason || '',
            actions: msg.actions || ['yes', 'no'],
          },
        }]);
        break;
      }

      // ─── Agentic loop events ──────────────────────────────────

      case 'agentic_start': {
        if (msg.sub_agent === true) break;

        if (msg.autonomous) {
          this.backgroundTaskActive.set(true);
          this._backgroundTaskId = Date.now();
          this.activities.update(acts => [{
            id: this._backgroundTaskId, kind: 'todo' as any,
            text: msg.task_preview || 'Working on background task...',
            detail: `0/${msg.max_steps || 15} steps`,
            tags: [], signals: 0, factsStored: 0, timestamp: new Date(),
            metadata: { autonomous: true, step: 0, maxSteps: msg.max_steps || 15, source: msg.source || 'system' },
          }, ...acts].slice(0, 20));
          this._persistBackgroundState();
          break;
        }

        // Foreground (user-initiated) task: show in chat as before
        this.agenticActive.set(true);
        this.agenticStopping.set(false);
        this.agenticStep.set(0);
        this.agenticMaxSteps.set(msg.max_steps || 15);
        this.activityStatus.set('Starting task...');
        this.lastAgenticResult.set(null);
        this.planSteps.set([]);
        this.planExpanded.set(false);
        this._activePlanStepIdx = -1;
        this._agenticStepEvents = [];
        this._preToolReasoning = '';
        this._iterTextCommitted = false;
        this._pendingIterText = '';
        this.messages.update(msgs => [...msgs, {
          type: 'agentic_start' as any,
          content: `Agent starting task (up to ${msg.max_steps || 15} steps)`,
          timestamp: new Date(),
        }]);
        break;
      }

      case 'agentic_iteration': {
        if (msg.sub_agent === true) break;

        if (msg.autonomous) {
          const bgStep = msg.step || 0;
          const bgMax = msg.max_steps || 15;
          const bgTools = (msg.tool_calls || []).map((tc: any) => tc.name).join(', ');

          // Ensure activity card exists (fallback if agentic_start was missed)
          if (!this._backgroundTaskId || !this.activities().some(a => a.id === this._backgroundTaskId)) {
            this.backgroundTaskActive.set(true);
            this._backgroundTaskId = Date.now();
            this.activities.update(acts => [{
              id: this._backgroundTaskId, kind: 'todo' as any,
              text: msg.task_preview || 'Working on background task...',
              detail: `Step ${bgStep}/${bgMax}: ${bgTools || 'processing'}`,
              tags: [], signals: 0, factsStored: 0, timestamp: new Date(),
              metadata: { autonomous: true, step: bgStep, maxSteps: bgMax, source: msg.source || 'system' },
            }, ...acts].slice(0, 20));
          } else {
            this.activities.update(acts => acts.map(a =>
              a.id === this._backgroundTaskId
                ? { ...a, detail: `Step ${bgStep}/${bgMax}: ${bgTools || 'processing'}`,
                    metadata: { ...a.metadata, step: bgStep, maxSteps: bgMax } }
                : a
            ));
          }

          this._persistBackgroundState();

          if (msg.hormones || msg.working_memory) {
            this.nlsMetadata.update(m => {
              const updated: any = { ...m };
              if (msg.hormones) updated.hormones = msg.hormones;
              if (msg.working_memory) updated.working_memory = msg.working_memory;
              return updated;
            });
          }
          break;
        }

        // Foreground task: show in chat
        const step = msg.step || 0;
        const maxSteps = msg.max_steps || this.agenticMaxSteps();
        this.agenticStep.set(step);
        this.agenticMaxSteps.set(maxSteps);

        const toolCalls = msg.tool_calls || [];
        const toolResults = msg.tool_results || [];
        const toolNames = toolCalls.map((tc: any) => tc.name).join(', ');
        const successes = toolResults.filter((r: any) => r.success).length;

        // Reset the per-iteration commit flag so that text generated in a
        // text-only iteration (no tool calls) can be committed here even if
        // a previous tool-call iteration already set the flag. The flag is only
        // needed to prevent double-commits *within* a single iteration, but
        // streamingText is already cleared before agentic_iteration fires in
        // tool-call iterations, so resetting here is safe.
        this._iterTextCommitted = false;

        // Commit any accumulated streaming text from this iteration as a
        // visible message before it gets cleared for the next step.
        // _pendingIterText holds text that was saved when tool_execution_start
        // fired (before streamingText was cleared).
        const iterText = this._pendingIterText || this.streamingText();
        const iterReasoning = this.streamingReasoning();
        this._pendingIterText = '';
        if (iterText && !this._iterTextCommitted) {
          const thought = parseThinking(iterText);
          this.messages.update(msgs => [...msgs, {
            type: 'assistant' as any,
            content: thought.response || iterText,
            reasoning: (iterReasoning || thought.thinking) || undefined,
            timestamp: new Date(),
            agenticStep: step,
          }]);
          this._iterTextCommitted = true;
        } else if (iterReasoning) {
          this._preToolReasoning = iterReasoning;
        }
        this.streamingText.set('');
        this.streamingReasoning.set('');

        this._agenticStepEvents.push({
          step,
          toolCalls: toolCalls.map((tc: any) => ({ name: tc.name || 'tool' })),
          toolResults: toolResults.map((tr: any) => ({ success: tr.success !== false })),
          durationMs: msg.duration_ms || 0,
        });

        this.messages.update(msgs => [...msgs, {
          type: 'agentic_iteration' as any,
          content: toolNames
            ? `Step ${step}/${maxSteps}: ${toolNames} (${successes}/${toolResults.length} succeeded)`
            : `Step ${step}/${maxSteps}`,
          timestamp: new Date(),
          agentic: {
            step,
            maxSteps,
            toolCalls,
            toolResults,
            hormones: msg.hormones || {},
            durationMs: msg.duration_ms || 0,
          },
        }]);

        if (msg.hormones || msg.working_memory) {
          this.nlsMetadata.update(m => {
            const updated: any = { ...m };
            if (msg.hormones) updated.hormones = msg.hormones;
            if (msg.working_memory) updated.working_memory = msg.working_memory;
            return updated;
          });
        }
        break;
      }

      case 'agentic_complete': {
        if (msg.sub_agent === true) break;

        if (msg.autonomous) {
          this.backgroundTaskActive.set(false);
          const bgAborted = msg.aborted || false;
          const bgSteps = msg.total_steps || 0;
          const bgTools = msg.total_tool_calls || 0;
          const bgDur = ((msg.duration_ms || 0) / 1000).toFixed(0);
          const bgLabel = bgAborted
            ? `Task stopped: ${msg.abort_reason || 'Cancelled'}`
            : `Task completed (${bgSteps} steps, ${bgTools} tool calls, ${bgDur}s)`;

          // Ensure activity card exists (fallback if agentic_start was missed)
          if (!this._backgroundTaskId || !this.activities().some(a => a.id === this._backgroundTaskId)) {
            this._backgroundTaskId = Date.now();
            this.activities.update(acts => [{
              id: this._backgroundTaskId, kind: 'todo' as any,
              text: bgAborted ? 'Task stopped' : 'Task completed',
              detail: bgLabel, tags: [], signals: 0, factsStored: 0, timestamp: new Date(),
              metadata: { autonomous: true, completed: true, aborted: bgAborted, source: msg.source || 'system' },
            }, ...acts].slice(0, 20));
          } else {
            this.activities.update(acts => acts.map(a =>
              a.id === this._backgroundTaskId
                ? { ...a, text: a.text.replace(/Working on /, 'Completed: ').replace(/\.\.\.$/, ''),
                    detail: bgLabel, metadata: { ...a.metadata, completed: true, aborted: bgAborted } }
                : a
            ));
          }
          // Show a compact notification in chat so user knows something finished
          this.messages.update(msgs => [...msgs, {
            type: 'status' as any,
            content: bgAborted
              ? `Background task stopped: ${msg.abort_reason || 'Cancelled'}`
              : `Background task completed (${bgSteps} steps, ${bgDur}s)`,
            timestamp: new Date(),
          }]);
          sessionStorage.removeItem(`bg_task_${this.agentId}`);
          break;
        }

        // Foreground task: show full details in chat
        // Commit any remaining streaming text before clearing
        const remainingText = this.streamingText();
        const remainingReasoning = this.streamingReasoning();
        if (remainingText) {
          const thought = parseThinking(remainingText);
          this.messages.update(msgs => [...msgs, {
            type: 'assistant' as any,
            content: thought.response || remainingText,
            reasoning: (remainingReasoning || thought.thinking) || undefined,
            timestamp: new Date(),
          }]);
        }

        this.agenticActive.set(false);
        this.agenticStopping.set(false);
        this.askUserPending.set(false);
        this.activityStatus.set('');
        this.agenticStep.set(0);
        this.streamingText.set('');
        this.streamingReasoning.set('');
        this._pendingIterText = '';
        this.planExpanded.set(false);

        const aborted = msg.aborted || false;
        const totalSteps = msg.total_steps || 0;
        const totalToolCalls = msg.total_tool_calls || 0;
        const durationMs = msg.duration_ms || 0;
        const durationSec = (durationMs / 1000).toFixed(1);

        // Persist summary for the header chip
        this.lastAgenticResult.set({ steps: totalSteps, tools: totalToolCalls, durationMs, aborted });

        const stepEvents = [...this._agenticStepEvents];
        this._agenticStepEvents = [];

        this.messages.update(msgs => [...msgs, {
          type: 'agentic_complete' as any,
          content: aborted
            ? `Task stopped: ${msg.abort_reason || 'Cancelled'}`
            : `Task complete`,
          timestamp: new Date(),
          agenticComplete: {
            totalSteps,
            totalToolCalls,
            aborted,
            abortReason: msg.abort_reason || '',
            durationMs,
            hormones: msg.hormones || {},
            events: stepEvents,
          },
        }]);

        // Push final response as an assistant message if present and not
        // already committed from streaming (either in this handler or
        // from agentic_iteration which commits iterText then clears it)
        if (msg.final_response && !remainingText && !this._iterTextCommitted) {
          const thought = parseThinking(msg.final_response);
          const sk2 = msg.session_key || this.currentThread();
          this.messages.update(msgs => [...msgs, {
            type: 'assistant',
            content: thought.response || msg.final_response,
            reasoning: thought.thinking || undefined,
            timestamp: new Date(),
            nls: msg.nls,
            sessionKey: sk2 !== 'websocket:main' ? sk2 : undefined,
          }]);
        }

        // Flush buffered safety-net learnings onto the final message
        if (this._bufferedLearnings.length > 0) {
          const buffered = [...this._bufferedLearnings];
          this._bufferedLearnings = [];
          setTimeout(() => this._appendSignalTags(buffered), 100);
        }

        // Merge full NLS metadata (hormones, working_memory, etc.)
        if (msg.nls) {
          this.nlsMetadata.update(m => ({
            ...(m || {}),
            ...msg.nls,
            hormones: (msg.nls.hormones && Object.keys(msg.nls.hormones).length > 0)
              ? msg.nls.hormones
              : (m || {} as any).hormones ?? {},
            heartbeat: this._mergeHeartbeat(
              (m || {} as any).heartbeat,
              msg.nls.heartbeat,
            ),
            signals: [...((m || {} as any).signals || []), ...(msg.nls.signals || [])].slice(-20),
            sleep_count: (m || {} as any).sleep_count ?? 0,
            turn_count: ((m || {} as any).turn_count ?? 0) + 1,
          }));
        } else if (msg.hormones) {
          this.nlsMetadata.update(m => ({
            ...m,
            hormones: msg.hormones,
          }));
        }

        if (msg.working_memory) {
          this.nlsMetadata.update(m => ({
            ...m,
            working_memory: msg.working_memory,
          }));
        }
        break;
      }

      case 'activity_status': {
        const text = msg.text || msg.message || msg.content || '';
        const elapsed = msg.elapsed_ms || 0;
        if (msg.autonomous) {
          this._updateBackgroundCardDetail(elapsed ? `${text} (${(elapsed / 1000).toFixed(1)}s)` : text);
          break;
        }
        const statusType = msg.status || '';
        if (elapsed && text) {
          this.activityStatus.set(`${text} (${(elapsed / 1000).toFixed(1)}s)`);
        } else {
          this.activityStatus.set(text);
        }
        break;
      }

      case 'delegate_start': {
        if (msg.team_id) break;
        const dlgNum = msg.delegate_number ?? 0;
        const dlgTask = msg.delegate_task || 'Sub-task';
        const cardMsg: ChatMessage = {
          type: 'delegate_card',
          content: dlgTask,
          timestamp: new Date(),
          delegate: {
            number: dlgNum,
            task: dlgTask,
            status: 'running',
            toolCalls: [],
            expanded: true,
          },
        };
        this.messages.update(msgs => {
          this._activeDelegates.add(dlgNum);
          return [...msgs, cardMsg];
        });
        const buffered = this._delegateEventBuffer.get(dlgNum) || [];
        this._delegateEventBuffer.delete(dlgNum);
        for (const bufferedMsg of buffered) {
          this.handleRuntimeMessage(bufferedMsg);
        }
        this.activityStatus.set(`Delegating: ${dlgTask.slice(0, 80)}`);
        break;
      }

      case 'delegate_end': {
        if (msg.team_id) break;
        const dlgNum = msg.delegate_number ?? 0;
        const aborted = msg.aborted || false;
        const summary = msg.summary || '';
        const iterations = msg.iterations || 0;
        const toolCalls = msg.tool_calls || 0;
        this.messages.update(msgs => {
          const idx = this._findDelegateIdx(msgs, dlgNum);
          if (idx < 0) return msgs;
          const updated = [...msgs];
          const card = { ...updated[idx].delegate! };
          if (aborted && card.toolCalls?.length) {
            card.toolCalls = card.toolCalls.map((tc: any) =>
              tc.result ? tc : { ...tc, result: 'error', isError: true }
            );
          }
          updated[idx] = {
            ...updated[idx],
            delegate: {
              ...card,
              status: aborted ? 'error' : 'done',
              summary,
              iterations,
              totalToolCalls: toolCalls,
              expanded: false,
            },
          };
          return updated;
        });
        this._activeDelegates.delete(dlgNum);
        if (this._activeDelegates.size === 0) {
          this.activityStatus.set('');
        }
        break;
      }

      case 'delegate_batch_complete': {
        const count = msg.count || 0;
        const batchId = msg.batch_id || '';
        const resultsSummary = msg.results_summary || '';
        const notifMsg: ChatMessage = {
          type: 'system' as any,
          content: `All ${count} sub-agents completed (batch ${batchId}). Compiling results...`,
          timestamp: new Date(),
        };
        this.messages.update(msgs => [...msgs, notifMsg]);
        this.activityStatus.set('Compiling delegate results...');
        break;
      }

      case 'delegate_progress': {
        if (msg.team_id) break;
        const dlgNum = msg.delegate_number ?? -1;
        const iter = msg.iteration || 0;
        const maxIter = msg.max_iterations || 0;
        const elapsed = msg.elapsed_seconds || 0;
        this.messages.update(msgs => {
          const idx = this._findDelegateIdx(msgs, dlgNum);
          if (idx < 0) return msgs;
          const updated = [...msgs];
          const card = updated[idx].delegate!;
          updated[idx] = {
            ...updated[idx],
            delegate: {
              ...card,
              iterations: iter,
              maxIterations: maxIter,
              elapsedSeconds: Math.round(elapsed),
            },
          };
          return updated;
        });
        break;
      }

      case 'tool_output_chunk': {
        const chunk = msg.chunk || '';
        const toolName = msg.tool_name || 'bash';
        if (msg.sub_agent === true && msg.delegate_number != null && this._activeDelegates.has(msg.delegate_number)) {
          break;
        }
        if (msg.sub_agent === true && msg.delegate_number != null) {
          const buf = this._delegateEventBuffer.get(msg.delegate_number) || [];
          buf.push(msg);
          this._delegateEventBuffer.set(msg.delegate_number, buf);
          break;
        }
        this.messages.update(msgs => {
          const updated = [...msgs];
          // Append to the most recent bash tool_progress card if it exists
          for (let i = updated.length - 1; i >= 0; i--) {
            const m = updated[i] as any;
            if (m.type === 'tool_progress' && m.toolProgress?.toolName === 'bash' && !m.toolProgress?.done) {
              const prevOutput = m.toolProgress.output || '';
              updated[i] = {
                ...m,
                toolProgress: {
                  ...m.toolProgress,
                  output: prevOutput ? prevOutput + '\n' + chunk : chunk,
                },
              };
              return updated;
            }
          }
          // Fallback: accumulate into standalone chunk message
          const last = updated.length > 0 ? updated[updated.length - 1] : null;
          if (last && (last as any).type === 'tool_output_chunk' && (last as any).toolOutputName === toolName) {
            const prev = updated[updated.length - 1] as any;
            updated[updated.length - 1] = {
              ...prev,
              content: prev.content + '\n' + chunk,
              toolOutputChunk: prev.toolOutputChunk + '\n' + chunk,
            };
            return updated;
          }
          return [...updated, {
            type: 'tool_output_chunk' as any,
            content: chunk,
            toolOutputChunk: chunk,
            toolOutputName: toolName,
            timestamp: new Date(),
          }];
        });
        // Also append to the active plan step's detail
        this._appendPlanStepDetail(this._activePlanStepIdx, chunk + '\n');
        break;
      }

      case 'agentic_token': {
        if (msg.sub_agent === true || msg.autonomous) break;
        if (msg.thinking) {
          this.streamingReasoning.update(t => t + (msg.token || ''));
        } else {
          this.streamingText.update(t => t + (msg.token || ''));
        }
        break;
      }

      case 'tool_call_delta': {
        if (msg.sub_agent === true || msg.autonomous) break;
        const fnName = msg.function_name || '';
        const argsDelta = msg.arguments_delta || '';
        const idx = msg.index ?? 0;

        // Accumulate raw args JSON per tool call index
        if (!this._toolCallArgsAcc) this._toolCallArgsAcc = {};
        const isFirstDelta = !this._toolCallArgsAcc[idx];
        if (isFirstDelta) this._toolCallArgsAcc[idx] = { name: fnName, raw: '' };
        this._toolCallArgsAcc[idx].raw += argsDelta;
        if (fnName) this._toolCallArgsAcc[idx].name = fnName;

        const acc = this._toolCallArgsAcc[idx];

        // On first delta: capture any pre-tool content from streamingText.
        // This fires BEFORE turn_thinking and tool_execution_start, so we need
        // to separate actual <think> content (→ _preToolReasoning) from visible
        // response text (→ _pendingIterText) so they're rendered correctly:
        // thinking as expandable "Thought" cards, response text as assistant messages.
        if (isFirstDelta && idx === 0) {
          const raw = this.streamingText();
          if (raw) {
            const thinkRe = /<think>([\s\S]*?)<\/think>/g;
            const thinkParts: string[] = [];
            let m: RegExpExecArray | null;
            while ((m = thinkRe.exec(raw)) !== null) {
              if (m[1].trim()) thinkParts.push(m[1].trim());
            }
            const visibleText = raw
              .replace(/<think>[\s\S]*?<\/think>/g, '')
              .replace(/<\/?think>/g, '')
              .trim();

            if (thinkParts.length) {
              this._preToolReasoning = thinkParts.join('\n\n');
            }
            if (visibleText) {
              this._pendingIterText = visibleText;
            }
            this.streamingText.set('');
          }
        }

        // For write tools, show file content streaming
        if (acc.name === 'write' || acc.name === 'write_file' || acc.name === 'create_file') {
          // Extract path and content from partial JSON
          let filePath = '';
          let fileContent = '';
          const pathMatch = acc.raw.match(/"path"\s*:\s*"([^"]*)"/);
          if (pathMatch) filePath = pathMatch[1];
          const contentStart = acc.raw.indexOf('"content": "');
          if (contentStart >= 0) {
            // Everything after "content": " is the streaming content (minus trailing incomplete JSON)
            let raw = acc.raw.substring(contentStart + 12);
            // Strip trailing incomplete quote/brace if present
            if (raw.endsWith('"}')) raw = raw.slice(0, -2);
            else if (raw.endsWith('"')) raw = raw.slice(0, -1);
            fileContent = raw.replace(/\\n/g, '\n').replace(/\\t/g, '\t').replace(/\\"/g, '"').replace(/\\\\/g, '\\');
          }

          this.messages.update(msgs => {
            const updated = [...msgs];
            const last = updated.length > 0 ? updated[updated.length - 1] as any : null;
            if (last && last.type === 'tool_progress' && last.toolProgress?.toolName === acc.name && !last.toolProgress?.done) {
              updated[updated.length - 1] = {
                ...last,
                content: filePath ? `Writing ${filePath}...` : 'Writing file...',
                toolProgress: {
                  ...last.toolProgress,
                  arguments: { path: filePath, content: fileContent },
                },
              };
              return updated;
            }
            // Create new streaming file card
            return [...updated, {
              type: 'tool_progress' as any,
              content: filePath ? `Writing ${filePath}...` : 'Writing file...',
              toolProgress: {
                toolName: acc.name,
                callId: '',
                arguments: { path: filePath, content: fileContent },
                done: false,
                iteration: msg.iteration || 0,
              },
              timestamp: new Date(),
            }];
          });
          // Stream file content into the active plan step's detail
          if (fileContent && this._activePlanStepIdx >= 0) {
            this._updatePlanStep(this._activePlanStepIdx, { detail: fileContent });
          }
        } else if (acc.name === 'bash') {
          this.streamingText.set('');
          let command = '';
          const cmdMatch = acc.raw.match(/"command"\s*:\s*"([^"]*)"/);
          if (cmdMatch) command = cmdMatch[1].replace(/\\"/g, '"');

          if (command) {
            this.messages.update(msgs => {
              const updated = [...msgs];
              const last = updated.length > 0 ? updated[updated.length - 1] as any : null;
              if (last && last.type === 'tool_progress' && last.toolProgress?.toolName === 'bash' && !last.toolProgress?.done) {
                return updated;
              }
              return [...updated, {
                type: 'tool_progress' as any,
                content: `$ ${command}`,
                toolProgress: {
                  toolName: 'bash',
                  callId: '',
                  arguments: { command },
                  done: false,
                  iteration: msg.iteration || 0,
                },
                timestamp: new Date(),
              }];
            });
          }
        }
        break;
      }

      case 'turn_thinking': {
        if (msg.sub_agent === true || msg.autonomous) break;
        const streamedThinking = this.streamingReasoning();
        this.streamingReasoning.set('');
        const thinkingText = msg.thinking || streamedThinking || '';
        const current = this.streamingText();

        // For text-only iterations (no tools), keep response in streamingText.
        // For tool iterations, tool_call_delta already captured pre-tool text
        // into _preToolReasoning and cleared streamingText.
        let postThinkReasoning = '';
        if (current) {
          const thinkEnd = current.indexOf('</think>');
          if (thinkEnd >= 0) {
            postThinkReasoning = current.substring(thinkEnd + 8).trim();
            this.streamingText.set(postThinkReasoning);
          } else if (current.includes('<think>')) {
            this.streamingText.set('');
          }
        }

        // Merge any pre-tool reasoning captured by tool_call_delta
        if (this._preToolReasoning) {
          postThinkReasoning = postThinkReasoning
            ? postThinkReasoning + '\n\n' + this._preToolReasoning
            : this._preToolReasoning;
          this._preToolReasoning = '';
        }

        const fullThinking = [thinkingText, postThinkReasoning].filter(Boolean).join('\n\n');
        if (fullThinking.trim()) {
          const thinkingMsg = {
            type: 'turn_thinking' as any,
            content: fullThinking,
            thinking: fullThinking,
            thinkingIteration: msg.iteration || 0,
            timestamp: new Date(),
          };
          const iter = msg.iteration || 0;
          this.messages.update(msgs => {
            // Insert thinking BEFORE any tool_progress cards from
            // the same iteration so the UI shows think-then-act order.
            let insertIdx = msgs.length;
            for (let i = msgs.length - 1; i >= 0; i--) {
              const m = msgs[i] as any;
              if (m.type === 'tool_progress' && m.toolProgress?.iteration === iter) {
                insertIdx = i;
              } else {
                break;
              }
            }
            const updated = [...msgs];
            updated.splice(insertIdx, 0, thinkingMsg);
            return updated;
          });
        }
        break;
      }

      case 'agentic_plan': {
        const rawSteps: any[] = msg.steps || [];
        if (msg.autonomous) {
          if (rawSteps.length > 0) {
            const labels = rawSteps.map((s: any) =>
              typeof s === 'string' ? s : (s.label || '')).filter(Boolean);
            const statuses = rawSteps.map((s: any) =>
              typeof s === 'string' ? 'pending' : (s.status || 'pending'));
            const planId = msg.plan_id || '';

            // Only create a new card if this is a genuinely new plan
            // (not a re-broadcast after verify/update).
            const existingPlanCard = this.activities().find(
              a => a.metadata?.['planId'] === planId && planId,
            );
            if (!existingPlanCard) {
              this._addAutonomousActivityCard('agentic_task',
                `Plan created (${labels.length} steps)`,
                labels.slice(0, 4).join(', ') + (labels.length > 4 ? '...' : ''),
                { planId, planSteps: labels, planStatuses: statuses },
              );
            } else {
              // Update the existing card with latest step statuses
              existingPlanCard.metadata = {
                ...existingPlanCard.metadata,
                planStatuses: statuses,
              };
            }

            // Track plan steps for step-completed cards
            this._bgPlanSteps = labels;
            this._bgPlanStatuses = statuses;
          }
          break;
        }
        if (rawSteps.length > 0) {
          const hasIds = rawSteps.some((s: any) => typeof s === 'object' && s.id);
          this._planBackendDriven = hasIds;
          const parsed = rawSteps.map((s: any) =>
            typeof s === 'string'
              ? { label: s, status: 'pending' as const }
              : { label: s.label || '', status: (s.status === 'done' ? 'done' : s.status === 'failed' ? 'error' : s.status === 'in_progress' ? 'active' : 'pending') as any }
          );
          this.planSteps.set(parsed);
          this._planStepPrevStatus = parsed.map(s => s.status);
          this.planExpanded.set(true);
        }
        break;
      }

      case 'plan_step_update': {
        if (msg.autonomous) {
          const bgStepIdx = msg.step_index ?? -1;
          const bgStepStatus = msg.status || 'done';
          if (bgStepIdx >= 0 && bgStepIdx < this._bgPlanStatuses.length) {
            this._bgPlanStatuses[bgStepIdx] = bgStepStatus;
          }
          if (bgStepIdx >= 0 && bgStepStatus === 'done') {
            const stepLabel = bgStepIdx < this._bgPlanSteps.length
              ? this._bgPlanSteps[bgStepIdx] : '';
            this._addAutonomousActivityCard('agentic_task',
              `Step ${bgStepIdx + 1} completed${stepLabel ? ': ' + stepLabel : ''}`,
              undefined,
              { stepIndex: bgStepIdx, planSteps: [...this._bgPlanSteps], planStatuses: [...this._bgPlanStatuses] },
            );
          }
          break;
        }
        const stepIdx = msg.step_index ?? -1;
        const rawStatus = msg.status || 'done';
        const status = rawStatus === 'in_progress' ? 'active'
          : rawStatus === 'failed' ? 'error'
          : rawStatus === 'skipped' ? 'done'
          : rawStatus === 'done' ? 'done'
          : 'pending';
        if (stepIdx >= 0) {
          this._updatePlanStep(stepIdx, { status: status as any });
          if (stepIdx < this._planStepPrevStatus.length) {
            this._planStepPrevStatus[stepIdx] = status as any;
          }
        }
        break;
      }

      case 'tool_execution_start': {
        const toolName = msg.tool_name || '';
        const args = msg.arguments || {};

        if (msg.autonomous) {
          let bgLabel = toolName;
          if (toolName === 'write' || toolName === 'write_file' || toolName === 'create_file') {
            bgLabel = `Writing ${args.path || args.file_path || 'file'}...`;
          } else if (toolName === 'bash') {
            bgLabel = args.command ? `$ ${(args.command as string).slice(0, 60)}` : 'Running command...';
          } else if (toolName === 'read' || toolName === 'read_file') {
            bgLabel = `Reading ${args.path || args.file_path || 'file'}...`;
          } else if (toolName === 'edit') {
            bgLabel = `Editing ${args.path || args.file_path || 'file'}...`;
          } else if (toolName === 'plan') {
            bgLabel = `Plan: ${args.action || 'updating'}...`;
            this._updateBackgroundCardDetail(bgLabel);
            break;
          } else if (toolName === 'team') {
            bgLabel = `Team: ${args.action || 'managing'}...`;
          } else if (toolName === 'todo') {
            bgLabel = `Todo: ${args.action || 'updating'}...`;
          } else if (toolName === 'delegate') {
            bgLabel = `Delegating task...`;
          } else if (toolName === 'scheduler') {
            bgLabel = `Scheduler: ${args.action || 'managing'}...`;
          } else if (toolName === 'web_search' || toolName === 'search') {
            bgLabel = `Searching: ${args.query || args.term || ''}...`;
          } else if (toolName === 'browser_navigate') {
            bgLabel = `Browsing: ${(args.url || '').slice(0, 60)}...`;
          } else {
            bgLabel = `Running ${toolName}...`;
          }
          this._updateBackgroundCardDetail(bgLabel);
          this._addAutonomousActivityCard('agentic_task', bgLabel, undefined, {
            toolName, callId: msg.call_id || '',
          });
          break;
        }
        const callId = msg.call_id || '';
        const subAgent = msg.sub_agent === true;
        const dlgNum: number = msg.delegate_number ?? -1;

        if (subAgent) break;

        let label = toolName;
        if (toolName === 'write' || toolName === 'write_file' || toolName === 'create_file') {
          label = `Writing ${args.path || args.file_path || 'file'}...`;
        } else if (toolName === 'bash') {
          label = args.command ? `$ ${args.command}` : 'Running command...';
        } else if (toolName === 'read' || toolName === 'read_file') {
          label = `Reading ${args.path || args.file_path || 'file'}...`;
        } else if (toolName === 'edit') {
          label = `Editing ${args.path || args.file_path || 'file'}...`;
        } else if (toolName === 'offer_download') {
          label = `Preparing download: ${args.label || args.path || 'file'}...`;
        } else if (toolName === 'wait') {
          label = `Waiting ${args.seconds ?? 30}s…`;
        } else if (toolName === 'team') {
          label = `Team: ${args.action || 'managing'}...`;
        } else if (toolName === 'plan') {
          label = `Plan: ${args.action || 'updating'}...`;
        } else if (toolName === 'todo') {
          label = `Todo: ${args.action || 'updating'}...`;
        } else if (toolName === 'delegate') {
          label = `Delegating task...`;
        } else if (toolName === 'scheduler') {
          label = `Scheduler: ${args.action || 'managing'}...`;
        } else {
          label = `Running ${toolName}...`;
        }

        // Flush any unclaimed pre-tool reasoning as a thinking message
        // (covers the case where turn_thinking never fires, e.g. thinking disabled)
        if (this._preToolReasoning) {
          const reasoning = this._preToolReasoning;
          this._preToolReasoning = '';
          this.messages.update(msgs => [...msgs, {
            type: 'turn_thinking' as any,
            content: reasoning,
            thinking: reasoning,
            thinkingIteration: msg.iteration || 0,
            timestamp: new Date(),
          }]);
        }

        // Save any streaming text generated alongside this tool call before clearing.
        // Commit it immediately as a visible message so it doesn't vanish while
        // tools run and reappear after (no flicker). Only commit once per iteration
        // (first tool call in the turn) to avoid duplicate messages from parallel calls.
        // _pendingIterText may already be set by tool_call_delta (which fires first).
        const textToCommit = this._pendingIterText || (this.streamingText()?.trim() ? this.streamingText() : '');
        if (textToCommit && !this._iterTextCommitted) {
          const pendingThought = parseThinking(textToCommit);
          const pendingReasoning = this.streamingReasoning();
          this.messages.update(msgs => [...msgs, {
            type: 'assistant' as any,
            content: pendingThought.response || textToCommit,
            reasoning: (pendingReasoning || pendingThought.thinking) || undefined,
            timestamp: new Date(),
          }]);
          this.streamingReasoning.set('');
          this._pendingIterText = '';
          this._iterTextCommitted = true;
        }
        this.streamingText.set('');
        this._toolCallArgsAcc = {};
        this.messages.update(msgs => {
          const updated = [...msgs];
          for (let i = updated.length - 1; i >= 0; i--) {
            const m = updated[i] as any;
            if (m.type === 'tool_progress' && m.toolProgress?.toolName === toolName && !m.toolProgress?.done && !m.toolProgress?.callId) {
              updated[i] = {
                ...m,
                content: label,
                toolProgress: { ...m.toolProgress, callId, arguments: args },
              };
              return updated;
            }
          }
          return [...updated, {
            type: 'tool_progress' as any,
            content: label,
            toolProgress: {
              toolName,
              callId,
              arguments: args,
              done: false,
              iteration: msg.iteration || 0,
            },
            timestamp: new Date(),
          }];
        });

        if (this._activePlanStepIdx >= 0) {
          const prev = this._planStepPrevStatus[this._activePlanStepIdx] || 'pending';
          this._updatePlanStep(this._activePlanStepIdx, { status: prev });
        }
        this._activePlanStepIdx = this._matchToolToPlanStep(toolName, args);
        if (this._activePlanStepIdx >= 0) {
          this._updatePlanStep(this._activePlanStepIdx, { status: 'active' });
        }
        break;
      }

      case 'tool_execution_end': {
        if (msg.autonomous) {
          const bgToolName = msg.tool_name || '';
          const bgIsError = msg.is_error || false;
          const preview = msg.result_preview || '';
          const bgDoneLabel = bgIsError
            ? `${bgToolName} failed`
            : (preview ? `${bgToolName}: ${preview.slice(0, 100)}` : `${bgToolName} done`);
          this._updateBackgroundCardDetail(bgDoneLabel);
          break;
        }
        const callId = msg.call_id || '';
        const toolName = msg.tool_name || '';
        const isError = msg.is_error || false;
        const details = msg.details || null;
        const subAgent = msg.sub_agent === true;
        const dlgNum: number = msg.delegate_number ?? -1;

        if (subAgent) break;

        let doneLabel = toolName;
        if (toolName === 'write' || toolName === 'write_file' || toolName === 'create_file') {
          doneLabel = isError ? `Failed to write file` : `File written`;
        } else if (toolName === 'bash') {
          doneLabel = isError ? `Command failed` : `Command completed`;
        } else if (toolName === 'read' || toolName === 'read_file') {
          doneLabel = isError ? `Failed to read file` : `File read`;
        } else if (toolName === 'edit') {
          doneLabel = isError ? `Edit failed` : `File edited`;
        } else if (toolName === 'offer_download') {
          doneLabel = isError ? `Download failed` : `File ready`;
        } else if (toolName === 'team') {
          doneLabel = isError ? `Team action failed` : `Team updated`;
        } else if (toolName === 'plan') {
          doneLabel = isError ? `Plan action failed` : `Plan updated`;
        } else if (toolName === 'todo') {
          doneLabel = isError ? `Todo failed` : `Todo updated`;
        } else if (toolName === 'delegate') {
          doneLabel = isError ? `Delegation failed` : `Task delegated`;
        } else if (toolName === 'scheduler') {
          doneLabel = isError ? `Scheduler failed` : `Scheduled`;
        } else {
          doneLabel = isError ? `${toolName} failed` : `${toolName} done`;
        }

        this.messages.update(msgs => {
          const updated = [...msgs];
          let matchIdx = -1;
          for (let i = updated.length - 1; i >= 0; i--) {
            const m = updated[i] as any;
            if (m.type === 'tool_progress' && m.toolProgress?.callId === callId) {
              updated[i] = {
                ...m,
                content: doneLabel,
                toolProgress: {
                  ...m.toolProgress,
                  done: true,
                  isError,
                  ...(details ? { details } : {}),
                },
              };
              matchIdx = i;
              break;
            }
          }

          if (matchIdx >= 0) {
            const _GROUPABLE = new Set(['todo', 'plan', 'read', 'read_file', 'edit', 'write', 'write_file', 'create_file']);
            const cur = updated[matchIdx] as any;
            if (_GROUPABLE.has(toolName)) {
              const prev = matchIdx > 0 ? (updated[matchIdx - 1] as any) : null;
              if (
                prev?.type === 'tool_progress'
                && prev?.toolProgress?.done
                && prev?.toolProgress?.toolName === toolName
              ) {
                const existingItems = prev.toolProgress.groupItems || [{ label: prev.content, isError: prev.toolProgress.isError }];
                const newItems = [...existingItems, { label: doneLabel, isError }];
                const count = newItems.length;
                const groupLabel = toolName === 'todo' ? `${count} Todos updated`
                  : toolName === 'plan' ? `${count} Plans updated`
                  : toolName === 'edit' ? `${count} Files edited`
                  : `${count}× ${toolName}`;
                updated[matchIdx - 1] = {
                  ...prev,
                  content: groupLabel,
                  toolProgress: {
                    ...prev.toolProgress,
                    groupCount: count,
                    groupItems: newItems,
                    isError: newItems.some((it: any) => it.isError),
                  },
                };
                updated.splice(matchIdx, 1);
              }
            }
          }

          return updated;
        });

        if (this._activePlanStepIdx >= 0) {
          if (this._planBackendDriven) {
            const prev = this._planStepPrevStatus[this._activePlanStepIdx] || 'pending';
            this._updatePlanStep(this._activePlanStepIdx, { status: prev });
          } else {
            this._updatePlanStep(this._activePlanStepIdx, { status: isError ? 'error' : 'done' });
          }
          this._activePlanStepIdx = -1;
        }
        this.activityStatus.set('');
        break;
      }

      case 'browser_command': {
        const action = msg.action || 'navigate';
        const url = msg.url || '';
        const reqId = msg.request_id || '';
        console.log(`[CHAT] Received browser_command: action=${action} url=${url} reqId=${reqId}`);
        this.browserCommand.set({ ...msg });
        this.browserExpanded.set(true);

        // Only add a visible preview card for navigate actions
        if (action === 'navigate' && url) {
          this.messages.update(msgs => [...msgs, {
            type: 'browser_preview' as any,
            content: `Navigating to ${url}`,
            browserUrl: url,
            browserTitle: '',
            browserRequestId: reqId,
            timestamp: new Date(),
          }]);
        }
        break;
      }

      case 'browser_navigation': {
        this.browserExpanded.set(true);
        this.messages.update(msgs => [...msgs, {
          type: 'browser_navigation' as any,
          content: `${msg.action || 'navigate'}: ${msg.url || ''}`,
          browserUrl: msg.url || '',
          browserTitle: msg.title || '',
          browserAction: msg.action || '',
          timestamp: new Date(),
        }]);
        break;
      }

      case 'browser_auth_request': {
        const authUrl = msg.url || '';
        const authReqId = msg.request_id || '';
        this.messages.update(msgs => [...msgs, {
          type: 'status' as any,
          content: 'Opening your browser for sign-in — please complete it there, then click "Done" in the dialog.',
          timestamp: new Date(),
        }]);
        const nls = (window as any).nls;
        if (nls?.openAuthWindow) {
          nls.openAuthWindow(authUrl).then((result: any) => {
            this.messages.update(msgs => [...msgs, {
              type: 'status' as any,
              content: result?.success
                ? 'Sign-in confirmed. Continuing...'
                : 'Sign-in was cancelled or not completed.',
              timestamp: new Date(),
            }]);
            this.ws.send({
              type: 'browser_auth_response',
              request_id: authReqId,
              success: result?.success ?? false,
            });
          });
        } else {
          this.ws.send({
            type: 'browser_auth_response',
            request_id: authReqId,
            success: false,
            error: 'Auth window not available (non-Electron environment)',
          });
        }
        break;
      }

      case 'browser_set_cookies': {
        const cookies = msg.cookies || [];
        const reqId = msg.request_id || '';
        const nls = (window as any).nls;
        if (nls?.setBrowserCookies) {
          nls.setBrowserCookies(cookies).then((result: any) => {
            console.log(`[CHAT] browser:set-cookies result: ok=${result?.ok} fail=${result?.fail}`);
            this.ws.send({
              type: 'browser_set_cookies_response',
              request_id: reqId,
              ok: result?.ok ?? 0,
              fail: result?.fail ?? 0,
            });
          });
        }
        break;
      }

      case 'copilot_ack': {
        this.messages.update(msgs => [...msgs, {
          type: 'copilot_ack' as any,
          content: msg.message || 'Guidance received.',
          timestamp: new Date(),
        }]);
        break;
      }

      case 'ask_user': {
        this.askUserPending.set(true);
        this.messages.update(msgs => [...msgs, {
          type: 'ask_user',
          content: msg.question || 'I need more information to continue.',
          requestId: msg.request_id || '',
          timestamp: new Date(),
        }]);
        break;
      }

      case 'communicate': {
        this.streamingText.set('');
        this.messages.update(msgs => [...msgs, {
          type: 'assistant' as any,
          content: msg.message || '',
          timestamp: new Date(),
          midLoop: msg.mid_loop || false,
        }]);
        break;
      }

      case 'user_answer': {
        this.askUserPending.set(false);
        break;
      }

      case 'probe_signal': {
        const signals: Record<string, number> = msg.signals || {};
        const fired: string[] = msg.fired || [];
        const probeData = {
          signals,
          fired,
          iteration: msg.iteration || 0,
          midGeneration: msg.mid_generation || false,
          ts: new Date(),
        };
        this.latestProbeSignals.set(probeData);
        this.ws.emitProbeSignal(probeData);
        this.nlsMetadata.update(m => ({
          ...m,
          probe_signals: signals,
          probe_fired: fired,
        }));
        break;
      }

      case 'safety_net_learned': {
        const facts: string[] = msg.facts || [];
        const emotions: Record<string, number> = msg.emotions || {};
        const emotionTags = Object.entries(emotions)
          .filter(([, v]) => v >= 0.3)
          .map(([cat, v]) => {
            const intensity = v >= 0.7 ? 'high' : v >= 0.5 ? 'medium' : 'low';
            return `[${cat}:${intensity}]`;
          });
        const allTags = [...facts.map(f => `[LEARN:${f}]`), ...emotionTags];
        if (allTags.length === 0) break;

        if (this.agenticActive()) {
          this._bufferedLearnings.push(...allTags);
        } else {
          this._appendSignalTags(allTags);
        }
        this.signalSidebar?.onNewLearning();
        break;
      }

      case 'batch_update': {
        if (msg._batchDepth && msg._batchDepth > 5) break;
        const events = msg.events || [];
        for (const evt of events) {
          if (!evt || typeof evt !== 'object') continue;
          evt._batchDepth = (msg._batchDepth || 0) + 1;
          this.handleRuntimeMessage(evt);
        }
        break;
      }

      case 'sleep_start':
        // Dismiss any pending drowsy negotiation card — agent has accepted sleep
        this.messages.update(msgs => msgs.filter(m => m.type !== 'drowsy'));
        this.nlsMetadata.update(m => ({
          ...m,
          ans: { ...(m?.ans || {}), state: 'sleeping' },
        }));
        this.activities.update(acts => [{
          id: Date.now(), kind: 'sleep_start' as ActivityKind,
          text: msg.reason || 'Agent is sleeping...', tags: [],
          signals: 0, factsStored: 0, timestamp: new Date(),
        }, ...acts].slice(0, 15));
        break;

      case 'sleep_complete':
        this.nlsMetadata.update(m => ({
          ...m,
          ans: msg.ans ?? m?.ans ?? {},
          hormones: msg.hormones ?? m?.hormones ?? {},
          heartbeat: this._mergeHeartbeat(m?.heartbeat, msg.heartbeat),
          facts_in_memory: msg.facts_in_memory ?? m?.facts_in_memory ?? 0,
          working_memory: msg.working_memory ?? m?.working_memory,
          narrative: msg.narrative ?? m?.narrative,
          network_dynamics: msg.network_dynamics ?? m?.network_dynamics,
        }));
        this.activities.update(acts => [{
          id: Date.now(), kind: 'sleep_complete' as ActivityKind,
          text: 'Sleep cycle complete', tags: [],
          signals: msg.signals_processed || 0, factsStored: 0, timestamp: new Date(),
        }, ...acts].slice(0, 15));
        break;

      case 'consciousness_state':
        this.nlsMetadata.update(m => ({
          ...m,
          consciousness: msg.state || msg.consciousness || 'awake',
        }));
        break;

      case 'working_memory_update':
        this.nlsMetadata.update(m => ({
          ...m,
          working_memory: msg.working_memory || msg,
        }));
        break;

      case 'intention_triggered':
        this.activities.update(acts => [{
          id: Date.now(), kind: 'intention' as ActivityKind,
          text: msg.content || 'Intention triggered', tags: [],
          signals: 0, factsStored: 0, timestamp: new Date(),
          detail: msg.trigger || '',
        }, ...acts].slice(0, 15));
        break;

      case 'episode_start':
        this.nlsMetadata.update(m => ({
          ...m,
          narrative: {
            ...(m?.narrative || {}),
            current_episode: { title: msg.title || 'New episode', turns: 0, arc: '', peak_resonance: 0 },
          },
        }));
        this.activities.update(acts => [{
          id: Date.now(), kind: 'episode' as ActivityKind,
          text: `Episode started: ${msg.title || 'untitled'}`, tags: [],
          signals: 0, factsStored: 0, timestamp: new Date(),
        }, ...acts].slice(0, 15));
        break;

      case 'episode_end':
        this.nlsMetadata.update(m => ({
          ...m,
          narrative: {
            ...(m?.narrative || {}),
            current_episode: null,
            episode_count: ((m?.narrative as any)?.episode_count || 0) + 1,
          },
        }));
        this.activities.update(acts => [{
          id: Date.now(), kind: 'episode' as ActivityKind,
          text: `Episode ended: ${msg.title || msg.summary?.title || ''}`, tags: [],
          signals: 0, factsStored: 0, timestamp: new Date(),
          detail: msg.summary?.arc || '',
        }, ...acts].slice(0, 15));
        break;

      case 'network_switch':
        this.nlsMetadata.update(m => ({
          ...m,
          network_dynamics: {
            ...(m?.network_dynamics || {}),
            dominant: msg.to || msg.to_network || '',
            ecn: msg.ecn ?? (m?.network_dynamics as any)?.ecn ?? 0,
            sn: msg.sn ?? (m?.network_dynamics as any)?.sn ?? 0,
            dmn: msg.dmn ?? (m?.network_dynamics as any)?.dmn ?? 0,
          },
        }));
        this.activities.update(acts => [{
          id: Date.now(), kind: 'network' as ActivityKind,
          text: `Network: ${msg.from || '?'} → ${msg.to || '?'}`, tags: [],
          signals: 0, factsStored: 0, timestamp: new Date(),
        }, ...acts].slice(0, 15));
        break;

      case 'regulation_applied':
        this.activities.update(acts => [{
          id: Date.now(), kind: 'regulation' as ActivityKind,
          text: `Regulation: ${msg.strategy || 'applied'}`, tags: [],
          signals: 0, factsStored: 0, timestamp: new Date(),
        }, ...acts].slice(0, 15));
        break;

      case 'todo_update': {
        const item = msg.item || {};
        let todoText = item.title || 'Task';
        if (msg.action === 'completed') {
          todoText = `Completed: ${todoText}`;
        } else if (msg.action === 'added') {
          todoText = `New task: ${todoText}`;
        } else if (item.status === 'in_progress') {
          todoText = `Working on: ${todoText}`;
        } else if (msg.action === 'updated' && item.notes) {
          const notePreview = item.notes.length > 60 ? item.notes.slice(0, 60) + '...' : item.notes;
          todoText = `Progress: ${item.title} — ${notePreview}`;
        }
        this.activities.update(acts => [{
          id: Date.now(), kind: 'todo' as ActivityKind,
          text: todoText, tags: [],
          signals: 0, factsStored: 0, timestamp: new Date(),
          metadata: { todoId: item.id, action: msg.action },
        }, ...acts].slice(0, 15));
        break;
      }

      case 'prediction_error':
        this.nlsMetadata.update(m => ({
          ...m,
          predictive_processing: {
            ...(m?.predictive_processing || {}),
            average_pe: msg.pe ?? (m?.predictive_processing as any)?.average_pe ?? 0,
          },
        }));
        break;

      case 'connection_request':
        if (msg.skill === 'google-workspace') {
          this.googleModalOpen.set(true);
        }
        break;
    }
  }

  // ─── In-app browser ───────────────────────────────────────

  private _appendLearnTags(facts: string[]): void {
    this._appendSignalTags(facts.map(f => `[LEARN:${f}]`));
  }

  private _appendSignalTags(tags: string[]): void {
    if (tags.length === 0) return;
    this.messages.update(msgs => {
      const updated = [...msgs];
      for (let i = updated.length - 1; i >= 0; i--) {
        const t = updated[i].type;
        if (t === 'assistant' || t === ('agentic_complete' as any)) {
          const tagStr = tags.map(t => ` ${t}`).join('');
          updated[i] = {
            ...updated[i],
            content: (updated[i].content || '') + tagStr,
          };
          break;
        }
      }
      return updated;
    });
  }

  // ─── Google Workspace connect modal ─────────────────────

  onGoogleModalClosed(): void {
    this.googleModalOpen.set(false);
  }

  onGoogleConnected(event: { email: string }): void {
    this.ws.send({ type: 'connection_complete', skill: 'google-workspace', email: event.email });
  }

  onBrowserResult(response: any): void {
    const reqId = response.requestId || response.request_id || '';
    console.log(`[CHAT] onBrowserResult: action=${response.action} status=${response.status} reqId=${reqId}`, response);

    // Update navigate preview cards with title/error
    if (response.action === 'navigate' && reqId) {
      this.messages.update(msgs => {
        const updated = [...msgs];
        for (let i = updated.length - 1; i >= 0; i--) {
          const m = updated[i] as any;
          if (m.type === 'browser_preview' && m.browserRequestId === reqId) {
            updated[i] = {
              ...m,
              browserTitle: response.title || m.browserUrl,
              browserError: response.status === 'error' ? response.error : undefined,
            };
            break;
          }
        }
        return updated;
      });
    }

    // Send response back to Python backend via WS
    const wsPayload: any = {
      type: 'browser_response',
      request_id: reqId,
      status: response.status || 'ok',
      result: response.result || '',
      title: response.title || '',
      url: response.url || '',
      error: response.error || '',
    };
    // Forward image data for screenshot_raw action
    if (response.image_base64) {
      wsPayload['image_base64'] = response.image_base64;
    }
    console.log(`[CHAT] Sending browser_response via WS:`, wsPayload);
    this.ws.send(wsPayload);
  }

  onExpandBrowser(msg: any): void {
    if (msg.browserUrl) {
      this.browserCommand.set({
        type: 'browser_command',
        action: 'navigate',
        url: msg.browserUrl,
        request_id: msg.browserRequestId || '',
      });
    }
    this.browserExpanded.set(true);
  }

  onOpenUrl(url: string): void {
    this.browserCommand.set({
      type: 'browser_command',
      action: 'navigate',
      url,
      request_id: '',
    });
    this.browserExpanded.set(true);
  }

  collapseBrowser(): void {
    this.browserExpanded.set(false);
  }

  onMessageFeedback(event: {
    messageIndex: number;
    content: string;
    messageContent: string;
    feedbackType: 'SPECIFIC' | 'GLOBAL';
    channel?: string;
    sessionKey?: string;
  }): void {
    const agentId = this.agent()?.id;
    if (!agentId) return;
    this.api.submitMessageFeedback(agentId, {
      comment: event.content,
      messageContent: event.messageContent,
      feedbackType: event.feedbackType,
      channel: event.channel,
      sessionKey: event.sessionKey || this.currentThread(),
    }).subscribe({
      next: () => {
        this.toast.show('Feedback sent', 'info');
      },
      error: (err) => {
        console.error('Failed to submit feedback:', err);
        this.toast.show('Failed to send feedback', 'error');
      },
    });
  }

  // -- Thread switcher ----------------------------------------------------

  activeThreadLabel(): string {
    const current = this.currentThread();
    const thread = this.activeThreads().find(t => t.key === current);
    return thread?.label || 'Main Chat';
  }

  activeThreadIcon(): string {
    const current = this.currentThread();
    const thread = this.activeThreads().find(t => t.key === current);
    return thread?.channel || 'websocket';
  }

  threadCount(): number {
    return this.activeThreads().length;
  }

  toggleThreadDropdown(): void {
    this.threadDropdownOpen.update(v => !v);
  }

  switchThread(key: string): void {
    this.currentThread.set(key);
    this.threadDropdownOpen.set(false);
    this.loadThreadHistory(key);
  }

  createNewThread(): void {
    const id = Date.now().toString(36);
    const key = `websocket:thread:${id}`;
    const count = this.activeThreads().filter(t => t.channel === 'websocket').length;
    const label = `Thread ${count}`;
    this.activeThreads.update(threads => [...threads, { key, label, channel: 'websocket' }]);
    this.currentThread.set(key);
    this.threadDropdownOpen.set(false);
  }

  /** Restore thread list from the runtime's persisted session index. */
  private loadPersistedThreads(): void {
    if (!this.agentId) return;

    const url = this.platform.isElectron
      ? `${(window as any).nls?.runtimeUrl || 'http://127.0.0.1:9222'}/sessions/${this.agentId}`
      : `${this.api.apiBase}/agents/${this.agentId}/sessions`;

    this.http.get<any>(url).subscribe({
      next: (res) => {
        const sessions: Record<string, any> = res?.sessions || {};
        const restored: { key: string; label: string; channel: string; sender?: string; subject?: string }[] = [];
        for (const [key, meta] of Object.entries(sessions) as [string, any][]) {
          if (key === 'websocket:main') continue;
          const channel = meta?.channel || key.split(':')[0] || 'websocket';
          if (channel === 'team' || channel === 'delegate') continue;
          const sender = meta?.sender || '';
          const subject = meta?.subject || '';
          const label = subject
            ? subject.replace(/^re:\s*/i, '').substring(0, 40)
            : sender
              ? (channel === 'telegram' ? `@${sender}` : sender)
              : this.labelFromSessionKey(key, channel);
          restored.push({ key, label, channel, sender, subject });
        }
        if (restored.length > 0) {
          this.activeThreads.update(threads => {
            const existingKeys = new Set(threads.map(t => t.key));
            const newThreads = restored.filter(r => !existingKeys.has(r.key));
            return [...threads, ...newThreads];
          });
        }
      },
      error: () => {},
    });
  }

  private labelFromSessionKey(key: string, channel: string): string {
    const parts = key.split(':');
    switch (channel) {
      case 'email': {
        const threadId = parts.slice(2).join(':') || 'Thread';
        return `Re: ${threadId.substring(0, 30)}`;
      }
      case 'telegram':
        return parts[2] ? `@${parts[2]}` : 'DM';
      case 'whatsapp':
        return parts[2] || 'Chat';
      case 'websocket': {
        const idx = this.activeThreads().filter(t => t.channel === 'websocket').length;
        return parts[2] ? `Thread ${parts[2].substring(0, 8)}` : `Thread ${idx}`;
      }
      default:
        return parts.slice(1).join(':') || 'Thread';
    }
  }

  private loadThreadHistory(sessionKey: string): void {
    if (sessionKey === 'websocket:main') return;
    if (!this.agentId) return;

    const url = this.platform.isElectron
      ? `${(window as any).nls?.runtimeUrl || 'http://127.0.0.1:9222'}/sessions/${this.agentId}/${encodeURIComponent(sessionKey)}`
      : `${this.api.apiBase}/agents/${this.agentId}/sessions/${encodeURIComponent(sessionKey)}`;

    const threadMeta = this.activeThreads().find(t => t.key === sessionKey);
    const isChannel = threadMeta && threadMeta.channel !== 'websocket';

    this.http.get<any>(url).subscribe({
      next: (res) => {
        if (!res?.messages?.length) return;

        let restored: ChatMessage[];
        if (isChannel) {
          restored = res.messages.map((m: any) => ({
            type: m.role === 'user' ? 'channel_inbound' as any : 'channel_outbound' as any,
            content: m.content || '',
            channel: threadMeta.channel,
            sender: threadMeta.sender || threadMeta.channel,
            subject: threadMeta.subject || '',
            sessionKey,
            timestamp: new Date(m.timestamp || Date.now()),
          }));
        } else {
          restored = res.messages.map((m: any) => ({
            type: m.role === 'user' ? 'user' as const : 'assistant' as const,
            content: m.content || '',
            timestamp: new Date(m.timestamp || Date.now()),
            sessionKey,
          }));
        }

        this.messages.update(msgs => {
          const existing = msgs.filter(m => m.sessionKey === sessionKey);
          if (existing.length > 0) return msgs;
          return [...msgs, ...restored];
        });
      },
      error: () => {},
    });
  }

  /** Called when a channel_event arrives via WebSocket */
  handleChannelEvent(event: any): void {
    const channel = event.channel || '';
    const sessionKey = event.session_key || '';
    if (!sessionKey) return;

    const existing = this.activeThreads().find(t => t.key === sessionKey);
    if (!existing) {
      const label = this.buildThreadLabel(channel, event);
      this.activeThreads.update(threads => [
        ...threads,
        { key: sessionKey, label, channel, sender: event.sender || '', subject: event.subject || '' },
      ]);
    } else if (!existing.subject && event.subject) {
      this.activeThreads.update(threads =>
        threads.map(t => t.key === sessionKey ? { ...t, subject: event.subject, sender: event.sender || t.sender } : t)
      );
    }

    const sender = event.sender || channel;
    const direction = event.direction || 'inbound';
    const inboundContent = event.content || event.content_preview || '';

    // Only show the inbound bubble on the "inbound" direction event.
    // The later "response" event re-sends the same content field —
    // we must not create a second bubble from it.
    if (inboundContent && direction === 'inbound') {
      this.messages.update(msgs => {
        // Dedup: skip if we already have this exact inbound message
        const alreadyShown = msgs.some(m =>
          m.type === 'channel_inbound' && (m as any).sessionKey === sessionKey && m.content === inboundContent
        );
        if (alreadyShown) return msgs;

        // Remove any plain 'user' type message loaded from history that duplicates
        // this real-time channel message (race between history load and live event).
        const deduped = msgs.filter(m =>
          !(m.type === 'user' && (m as any).sessionKey === sessionKey && m.content === inboundContent)
        );
        return [...deduped, {
          type: 'channel_inbound',
          content: inboundContent,
          channel,
          sender,
          subject: event.subject || '',
          sessionKey,
          timestamp: new Date(),
        }];
      });
    }
    if (event.response) {
      this.messages.update(msgs => {
        // Same dedup for assistant messages that were loaded as plain 'assistant' type
        const deduped = msgs.filter(m =>
          !(m.type === 'assistant' && (m as any).sessionKey === sessionKey && m.content === event.response)
        );
        return [...deduped, {
          type: 'channel_outbound',
          content: event.response,
          channel,
          sender,
          subject: event.subject || '',
          sessionKey,
          timestamp: new Date(),
        }];
      });
    }
  }

  private isSystemInjection(text: string): boolean {
    const t = text.trim();
    const patterns = [
      '[REMEMBERED',
      'Review: did you complete ALL parts',
      '[SKILL ONBOARDING',
      'Good plan. Now EXECUTE',
      'You wrote files to the skills directory but did NOT call',
      'SELF-CORRECTION CHECKPOINT',
      'ANS CHECKPOINT',
      '--- ANS CHECKPOINT',
      'Do NOT describe what you would do',
      'MANDATORY — without it the skill will never load',
      'ERRORS OBSERVED:',
      'STRESS LEVEL: cortisol=',
    ];
    return patterns.some(p => t.includes(p));
  }

  private buildThreadLabel(channel: string, event: any): string {
    const sender = event.sender || '';
    switch (channel) {
      case 'telegram':
        return sender ? `@${sender}` : 'DM';
      case 'whatsapp':
        return sender || 'Chat';
      case 'email': {
        const subj = event.subject || '';
        return subj ? `Re: ${subj.replace(/^re:\s*/i, '').substring(0, 40)}` : sender || 'Thread';
      }
      default:
        return sender || 'Thread';
    }
  }
}
