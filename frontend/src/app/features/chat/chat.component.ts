import { Component, OnInit, OnDestroy, AfterViewInit, signal, computed, ViewChild, ElementRef, AfterViewChecked, inject, effect } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import { ActivatedRoute, NavigationEnd, Router } from '@angular/router';
import { filter } from 'rxjs/operators';
import { Subscription, firstValueFrom } from 'rxjs';
import { WebSocketService, ChatMessage } from '../../core/services/websocket.service';
import { ChatUiSnapshotService } from '../../core/services/chat-ui-snapshot.service';
import { ChatWorkbenchService } from '../../core/services/chat-workbench.service';
import {
  collectFilePaths,
  fileDisplayName,
  normalizeToolArguments,
  parseAgentMessageText,
} from '../../core/services/activity-format.util';
import { toolWorkbenchTitle } from '../../core/services/workbench-labels.util';
import {
  resolveToolDisplayOutcome,
  toolDoneLabel,
} from '../../core/services/workbench-tool-outcome.util';
import { ApiService, FileAttachment, ProjectProcess } from '../../core/services/api.service';
import { ChatAttachmentService } from '../../core/services/chat-attachment.service';
import { isFolderAttachment } from '../../core/utils/chat-drop.util';
import { PlatformService } from '../../core/services/platform.service';
import { VoiceRecorderService } from '../../core/services/voice-recorder.service';
import { ToastService } from '../../shared/toast/toast.service';
import { filterNewLearnTags, labelTags, parseTags, parseThinking } from '../../shared/signal-utils';
import { Agent } from '../../core/models/agent.model';
import { MessageListComponent } from './message-list/message-list.component';
import { SignalSidebarComponent, ActivityKind } from './signal-sidebar/signal-sidebar.component';
import { GoogleConnectModalComponent } from '../../shared/google-connect-modal/google-connect-modal.component';
import { PlatformIntegrationsService } from '../../core/services/platform-integrations.service';
import { googleUsesByo } from '../../core/services/platform-integrations.util';
import { RunPanelComponent } from './run-panel/run-panel.component';
import { RunViewService } from '../../core/services/run-view.service';
import type { RunDelegate } from '../../core/models/run-view.model';
import { AgentWorkspaceContextService } from '../../core/services/agent-workspace-context.service';
import { enrichWorkspaceRelativePath } from '../projects/workspace/workspace-path.util';
import { Day1CoachService } from '../../shared/onboarding/day1-coach.service';
import { AgentModelService } from '../../core/services/agent-model.service';
import { AgentOrchestrationProfileService } from '../../core/services/agent-orchestration-profile.service';
import { ChatModelPickerComponent } from './chat-model-picker/chat-model-picker.component';
import { ChatOrchestrationProfilePickerComponent } from './chat-orchestration-profile-picker/chat-orchestration-profile-picker.component';
import {
  agenticAbortLabel,
  isOrchestrationDispatchSource,
  isSilentAutonomousCompletion,
  isUserFacingOrchestrationMessage,
  isSilentOrchestrationExit,
} from './orchestration-ui.util';
import { ChatPanelService } from '../../core/services/chat-panel.service';
import { ConversationService, ConversationThread } from '../../core/services/conversation.service';
import { composerDestination } from '../../core/services/composer-destination.util';
import { ChatMainTranscriptService } from '../../core/services/chat-main-transcript.service';
import { restoreChatMessagesFromTranscript, isChatSystemInjection } from '../../core/services/chat-transcript-restore.util';
import { ChatLeftDockComponent } from './chat-left-dock/chat-left-dock.component';
import { ChatRightDockComponent } from './chat-right-dock/chat-right-dock.component';
import { ConversationNavComponent } from './conversation-nav/conversation-nav.component';
import { ConversationBreadcrumbComponent } from './conversation-breadcrumb/conversation-breadcrumb.component';
import { ChatInboxComponent } from './chat-inbox/chat-inbox.component';
import { ConversationContextComponent } from './conversation-context/conversation-context.component';

export { agenticAbortLabel } from './orchestration-ui.util';

@Component({
  selector: 'app-chat',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    MessageListComponent,
    SignalSidebarComponent,
    GoogleConnectModalComponent,
    RunPanelComponent,
    ChatModelPickerComponent,
    ChatOrchestrationProfilePickerComponent,
    ChatLeftDockComponent,
    ChatRightDockComponent,
    ConversationNavComponent,
    ConversationBreadcrumbComponent,
    ChatInboxComponent,
    ConversationContextComponent,
  ],
  templateUrl: './chat.component.html',
  styleUrl: './chat.component.scss',
})
export class ChatComponent implements OnInit, AfterViewInit, OnDestroy, AfterViewChecked {
  private static readonly NEURAL_SIDEBAR_KEY = 'nls_neural_sidebar_open';

  @ViewChild('messageInput') messageInput!: ElementRef<HTMLTextAreaElement>;
  @ViewChild('waveformCanvas') waveformCanvas!: ElementRef<HTMLCanvasElement>;
  @ViewChild('fileInput') fileInput!: ElementRef<HTMLInputElement>;
  @ViewChild('folderInput') folderInput!: ElementRef<HTMLInputElement>;
  @ViewChild(SignalSidebarComponent) signalSidebar?: SignalSidebarComponent;
  @ViewChild('messageList') messageList?: MessageListComponent;
  agent = signal<Agent | null>(null);
  agentOnline = signal(true);
  messages = signal<ChatMessage[]>([]);
  inputText = '';
  /** @deprecated Use panels.rightDockOpen(); kept for snapshot restore bridge. */
  sidebarOpen = signal(ChatComponent.readNeuralSidebarPreference());
  readonly panels = inject(ChatPanelService);
  readonly conversations = inject(ConversationService);
  streamingText = signal('');
  streamingReasoning = signal('');
  /** True after the user sends until the agent shows streaming or a reply. */
  awaitingResponse = signal(false);
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
  private _toolTurnActive = false;  // true once tool_call_delta starts — prose buffers, not live-streamed

  /** User message sent or reply streaming — show stop control in composer. */
  readonly generationActive = computed(() =>
    this.awaitingResponse()
    || !!this.streamingText().trim()
    || !!this.streamingReasoning().trim()
    || this.agenticActive(),
  );

  readonly stopInFlight = computed(() =>
    this.generationStopping() || this.agenticStopping(),
  );

  bottomSheetOpen = signal(false);
  mobileConvNavOpen = signal(false);
  daydreams = signal<any[]>([]);
  activities = signal<any[]>([]);

  // Agentic loop state
  agenticActive = signal(false);
  agenticStep = signal(0);
  agenticMaxSteps = signal(0);
  activityStatus = signal('');
  projectProcesses = signal<ProjectProcess[]>([]);
  projectProcessesOpen = signal(false);
  private projectProcessesPoll: ReturnType<typeof setInterval> | null = null;
  agenticStopping = signal(false);
  generationStopping = signal(false);
  lastAgenticResult = signal<{ steps: number; tools: number; durationMs: number; aborted: boolean } | null>(null);
  backgroundTaskActive = signal(false);
  private _backgroundTaskId: number = 0;
  private _pendingDrowsyIndex: number | null = null;
  /** Suppress activity-sidebar writes while handling delegate (sub_agent) events. */
  private _activitySidebarSuppressDepth = 0;
  private _bgPlanSteps: string[] = [];
  private _bgPlanStatuses: string[] = [];


  // Ask-user state (agent waiting for human input)
  askUserPending = signal(false);
  budgetPromptPending = signal(false);
  private _pendingBudgetIndex: number | null = null;

  // Google Workspace connect modal (triggered by agent or UI)
  googleModalOpen = signal(false);

  // In-app browser state (left dock tab)
  browserCommand = signal<any>(null);

  // File attachment state
  pendingAttachments = signal<FileAttachment[]>([]);
  isDragOver = signal(false);
  fileUploading = signal(false);

  // Thread / conversation state
  currentThread = signal<string>('websocket:main');
  readonly activeThreads = computed(() => this.conversations.threads());
  readonly groupedThreads = computed(() => this.conversations.groupedThreads());

  /** Messages filtered by the currently selected thread */
  filteredMessages = computed(() => {
    const thread = this.currentThread();
    const msgs = this.messages();
    const threadFiltered = thread === 'websocket:main'
      ? msgs.filter(m => !m.sessionKey || m.sessionKey === 'websocket:main')
      : msgs.filter(m => m.sessionKey === thread);
    if (!this.runView.visible()) {
      return threadFiltered;
    }
    // Run workbench owns delegate cards and bash stream detail; keep tool chips
    // and assistant prose in chat so the thread stays readable.
    return threadFiltered.filter(m => {
      const t = (m as { type?: string }).type;
      if (t === 'delegate_card') return false;
      return true;
    });
  });
  activeThreadMeta = computed(() => {
    const key = this.currentThread();
    const t = this.conversations.threads().find(th => th.key === key);
    if (!t) return null;
    if (t.isGroup === undefined) {
      const flags = this.conversations.threadFlagsFromKey(key);
      return { ...t, isGroup: flags.isGroup };
    }
    return t;
  });

  composerDest = computed(() => composerDestination(this.activeThreadMeta()));

  /** Connected channel names for the sidebar status strip */
  connectedChannels = computed(() => {
    const threads = this.activeThreads();
    const channels = new Set(threads.map(t => t.channel).filter(c => c !== 'websocket'));
    return Array.from(channels);
  });

  // ANS safety net: buffer learnings during agentic tasks
  // so they attach to the completion message, not pre-task chat.
  private _bufferedLearnings: string[] = [];
  /** Session dedup for Learned chips (backend may still resend). */
  private _seenLearningKeys = new Set<string>();

  private _agenticStepEvents: { step: number; toolCalls: { name: string }[]; toolResults: { success: boolean }[]; durationMs: number }[] = [];

  private sub!: Subscription;
  private routerSub?: Subscription;
  private paramSub?: Subscription;
  agentId = '';
  private waveformAnimFrame = 0;

  constructor(
    private route: ActivatedRoute,
    private router: Router,
    public ws: WebSocketService,
    public api: ApiService,
    public platform: PlatformService,
    public voice: VoiceRecorderService,
    private toast: ToastService,
    private http: HttpClient,
    private chatUiSnapshot: ChatUiSnapshotService,
    readonly workbench: ChatWorkbenchService,
    readonly runView: RunViewService,
    private workspaceCtx: AgentWorkspaceContextService,
    private day1Coach: Day1CoachService,
    private agentModels: AgentModelService,
    readonly orchProfiles: AgentOrchestrationProfileService,
    private readonly mainTranscript: ChatMainTranscriptService,
    readonly platformIntegrations: PlatformIntegrationsService,
    private chatAttachments: ChatAttachmentService,
  ) {
    effect(() => {
      this.mainTranscript.revision();
      this.pullSharedMainTranscript();
    });
    effect(() => {
      this.messages();
      this.currentThread();
      this.syncMainTranscript();
    });
  }

  googleUsesByoCredentials(): boolean {
    return googleUsesByo(this.platformIntegrations.backendChoice());
  }

  private _enrichFilePaths(paths: string[]): string[] {
    const pd = this.workspaceCtx.getProjectDir(this.agentId);
    if (!pd) return paths;
    return paths.map((p) => enrichWorkspaceRelativePath(p, pd));
  }

  ngOnInit() {
    this.agentId = this.route.snapshot.params['agentId'];
    this._seenLearningKeys.clear();
    this.agentModels.bindAgent(this.agentId);
    this.orchProfiles.setActiveAgent(this.agentId);
    this.workbench.bindAgent(this.agentId);
    this.runView.bindAgent(this.agentId);
    this.restoreChatUiSnapshot();
    this.mergeMainTranscriptOnInit();
    this.hydrateRunFromApi();
    void this.agentModels.refreshFromConfig();
    void this.platformIntegrations.refresh();
    this.routerSub = this.router.events
      .pipe(filter((e): e is NavigationEnd => e instanceof NavigationEnd))
      .subscribe(() => {
        void this.agentModels.refreshFromConfig();
      });

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
            this.syncInboxBadge();
          }
        },
        error: () => this.agentOnline.set(false),
      });
    } else {
      this.ws.connect();
      this.ws.joinAgent(this.agentId);
      this.loadPersistedThreads();
      this.syncInboxBadge();
    }

    // Restore background task activity card if one was running before navigation
    this._restoreBackgroundState();

    // Listen for messages
    this.sub = this.ws.onMessage(this.agentId).subscribe((msg) => {
      this.handleRuntimeMessage(msg);
    });

    this.startProjectProcessesPoll();

    // Prefill from query param (used by skill escalation)
    const prefill = this.route.snapshot.queryParams['prefill'];
    if (prefill) {
      this.inputText = prefill;
      setTimeout(() => this.messageInput?.nativeElement?.focus(), 200);
    }

    // Skill setup from query param (used by settings channel cards)
    const setupSkill = this.route.snapshot.queryParams['setup'];
    if (setupSkill) {
      if (setupSkill === 'google-workspace' && !this.googleUsesByoCredentials()) {
        this.googleModalOpen.set(true);
      } else {
        this.triggerSkillSetup(setupSkill);
      }
    }

    this.paramSub = this.route.paramMap.subscribe((params) => {
      const nextId = params.get('agentId') ?? '';
      if (!nextId || nextId === this.agentId) return;
      this.switchChatAgent(nextId);
    });
  }

  /** When Angular reuses ChatComponent across /chat/:id navigations. */
  private switchChatAgent(nextId: string): void {
    this.persistChatUiSnapshot();
    this.sub?.unsubscribe();

    this.agentId = nextId;
    this._seenLearningKeys.clear();
    this.awaitingResponse.set(false);
    this.projectProcesses.set([]);
    this.projectProcessesOpen.set(false);
    this.agentModels.bindAgent(nextId);
    this.orchProfiles.setActiveAgent(nextId);
    this.workbench.bindAgent(nextId);
    this.runView.bindAgent(nextId);
    this.restoreChatUiSnapshot();
    this.mergeMainTranscriptOnInit();
    this.hydrateRunFromApi();
    this._restoreBackgroundState();

    this.api.getAgent(nextId).subscribe({
      next: (agent) => this.agent.set(agent),
    });

    this.ws.connect();
    this.ws.joinAgent(nextId);
    this.currentThread.set('websocket:main');
    this.loadPersistedThreads();

    this.sub = this.ws.onMessage(nextId).subscribe((msg) => {
      this.handleRuntimeMessage(msg);
    });

    this.startProjectProcessesPoll();
  }

  private startProjectProcessesPoll(): void {
    this.stopProjectProcessesPoll();
    void this.refreshProjectProcesses();
    this.projectProcessesPoll = setInterval(() => {
      void this.refreshProjectProcesses();
    }, 8000);
  }

  private stopProjectProcessesPoll(): void {
    if (this.projectProcessesPoll) {
      clearInterval(this.projectProcessesPoll);
      this.projectProcessesPoll = null;
    }
  }

  async refreshProjectProcesses(): Promise<void> {
    if (!this.agentId) return;
    try {
      const { processes, agentic_running } = await firstValueFrom(
        this.api.listProjectProcesses(this.agentId),
      );
      this.projectProcesses.set(processes);
      if (this.agenticActive() && !agentic_running) {
        this.clearStaleAgenticUi('server_idle');
      }
    } catch {
      // Runtime may be down or agent evicted from VRAM.
    }
  }

  /** Drop in-flight agentic UI when the server says the loop already ended. */
  private clearStaleAgenticUi(reason: string): void {
    if (!this.agenticActive() && !this.activityStatus() && !this.agenticStopping()) {
      return;
    }
    this.agenticActive.set(false);
    this.agenticStopping.set(false);
    this.askUserPending.set(false);
    this.budgetPromptPending.set(false);
    this.activityStatus.set('');
    this.agenticStep.set(0);
    this.streamingText.set('');
    this.streamingReasoning.set('');
    this._pendingIterText = '';
    this.clearAwaitingResponse();
    this.lastAgenticResult.update(prev => prev ?? {
      steps: this.agenticStep(),
      tools: 0,
      durationMs: 0,
      aborted: reason !== 'server_idle',
    });
  }

  toggleProjectProcessesMenu(event: MouseEvent): void {
    event.stopPropagation();
    this.projectProcessesOpen.update(v => !v);
  }

  killProjectProcess(pid: number, event?: MouseEvent): void {
    event?.stopPropagation();
    if (!this.agentId) return;
    this.api.killProjectProcess(this.agentId, pid).subscribe({
      next: (processes) => {
        this.projectProcesses.set(processes);
        if (processes.length === 0) {
          this.projectProcessesOpen.set(false);
        }
      },
      error: () => void this.refreshProjectProcesses(),
    });
  }

  projectProcessKindLabel(kind: string): string {
    switch (kind) {
      case 'backend': return 'Backend';
      case 'frontend': return 'Frontend';
      case 'interactive': return 'Interactive';
      default: return 'Server';
    }
  }

  ngAfterViewInit(): void {
    this.day1Coach.startIfScheduled();
    this.day1Coach.requestLayoutUpdate();
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
    this.stopProjectProcessesPoll();
    this.sub?.unsubscribe();
    this.routerSub?.unsubscribe();
    this.paramSub?.unsubscribe();
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
    this.runView.restorePersisted(snap.runView ?? null);
    if (snap.latestProbeSignals != null) {
      this.latestProbeSignals.set(snap.latestProbeSignals);
    }
    this.agenticActive.set(snap.agenticActive ?? false);
    this.askUserPending.set(snap.askUserPending ?? false);
    this.budgetPromptPending.set(snap.budgetPromptPending ?? false);
    this.agenticStep.set(snap.agenticStep ?? 0);
    this.agenticMaxSteps.set(snap.agenticMaxSteps ?? 0);
    this.activityStatus.set(snap.activityStatus ?? '');
    this.agenticStopping.set(snap.agenticStopping ?? false);
    this.backgroundTaskActive.set(snap.backgroundTaskActive ?? false);
    this.workbench.restoreState(
      snap.workbenchOpen ?? false,
      snap.workbenchEntries ?? [],
      snap.workbenchDensity,
    );
    if (snap.workbenchOpen) {
      this.panels.openLeft('workbench');
    }
    if (snap.sidebarOpen != null) {
      this.sidebarOpen.set(snap.sidebarOpen);
      if (snap.sidebarOpen) {
        this.panels.openRight('live');
      }
    }
  }

  /** Prefer shared main-thread transcript when Projects sidebar was active. */
  private mergeMainTranscriptOnInit(): void {
    if (!this.agentId) return;
    const shared = this.mainTranscript.get(this.agentId);
    const local = this.messages();
    if (shared.length > local.length) {
      this.messages.set(shared);
    } else if (local.length > 0) {
      this.syncMainTranscript();
    }
  }

  private syncMainTranscript(): void {
    if (!this.agentId || this.currentThread() !== 'websocket:main') return;
    const mainMsgs = this.messages().filter(
      m => !m.sessionKey || m.sessionKey === 'websocket:main',
    );
    this.mainTranscript.replace(this.agentId, mainMsgs);
  }

  /** Adopt newer main-thread transcript from Projects sidebar. */
  private pullSharedMainTranscript(): void {
    if (!this.agentId || this.currentThread() !== 'websocket:main' || this.agenticActive()) return;
    const shared = this.mainTranscript.get(this.agentId);
    const local = this.messages().filter(
      m => !m.sessionKey || m.sessionKey === 'websocket:main',
    );
    if (shared.length <= local.length) return;
    const branch = this.messages().filter(
      m => m.sessionKey && m.sessionKey !== 'websocket:main',
    );
    this.messages.set([...branch, ...structuredClone(shared)]);
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
    this.syncMainTranscript();
    this.chatUiSnapshot.save(this.agentId, {
      messages: this.messages(),
      nlsMetadata: this.nlsMetadata(),
      activities: this.activities(),
      daydreams: this.daydreams(),
      runView: this.runView.persisted(),
      latestProbeSignals: this.latestProbeSignals(),
      agenticActive: this.agenticActive(),
      askUserPending: this.askUserPending(),
      budgetPromptPending: this.budgetPromptPending(),
      agenticStep: this.agenticStep(),
      agenticMaxSteps: this.agenticMaxSteps(),
      activityStatus: this.activityStatus(),
      agenticStopping: this.agenticStopping(),
      backgroundTaskActive: this.backgroundTaskActive(),
      workbenchOpen: this.panels.leftDock() === 'workbench',
      workbenchEntries: this.workbench.snapshotState().entries,
      workbenchDensity: this.workbench.snapshotState().density,
      sidebarOpen: this.panels.rightDockOpen(),
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
    const dest = this.composerDest();
    const surfaceSend =
      dest.mode === 'surface'
      && !this.agenticActive()
      && !this.askUserPending()
      && !this.budgetPromptPending();

    if (surfaceSend) {
      const meta = this.activeThreadMeta();
      this.messages.update(msgs => [...msgs, {
        type: 'channel_outbound' as const,
        content: displayParts.join('\n') || '(attachment)',
        channel: meta?.channel || dest.surface || '',
        sender: 'You',
        sessionKey: threadKey,
        timestamp: new Date(),
        attachments: attachments.length > 0
          ? attachments.map(a => ({ name: a.name, path: a.path, mime_type: a.mime_type, size: a.size }))
          : undefined,
      }]);
      this.ws.sendChannelMessage(
        text || 'Please see the attached files.',
        threadKey,
        attachments.length > 0 ? attachments : undefined,
      );
      this.inputText = '';
      this.pendingAttachments.set([]);
      setTimeout(() => this.resetTextareaHeight(), 0);
      return;
    }

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

    // If the agent is waiting for a budget decision or ask_user answer, route as user_answer
    if (this.budgetPromptPending()) {
      this.ws.send({ type: 'user_answer', content: text, session_key: threadKey });
    } else if (this.askUserPending()) {
      this.ws.send({ type: 'user_answer', content: text, session_key: threadKey });
      this.askUserPending.set(false);
    } else {
      const model = this.agentModels.modelForOutgoingMessage();
      const orchProfile = this.orchProfiles.profileForOutgoingMessage(this.agentId);
      if (attachments.length > 0) {
        this.ws.send({
          type: 'message',
          content: text || 'Please examine the attached files.',
          attachments,
          session_key: threadKey,
          ...(model ? { model } : {}),
          ...(orchProfile ? { orchestration_profile: orchProfile } : {}),
        });
      } else {
        this.ws.sendMessage(text, threadKey, model, orchProfile);
      }
    }

    this.inputText = '';
    this.pendingAttachments.set([]);
    this.awaitingResponse.set(true);
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
  readonly isFolderAttachment = isFolderAttachment;

  onAttachClick(event: MouseEvent) {
    if (event.shiftKey) {
      this.folderInput?.nativeElement.click();
    } else {
      this.fileInput?.nativeElement.click();
    }
  }

  onFilesSelected(event: Event) {
    const input = event.target as HTMLInputElement;
    if (input.files && input.files.length > 0) {
      this.uploadFiles(Array.from(input.files));
    }
    input.value = '';
  }

  onFolderSelected(event: Event) {
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

    const dt = event.dataTransfer;
    if (!dt) return;
    this.uploadFromDataTransfer(dt);
  }

  removeAttachment(path: string) {
    this.pendingAttachments.update(list => list.filter(a => a.path !== path));
  }

  private uploadFiles(files: File[]) {
    if (!this.agentId || files.length === 0) return;
    this.fileUploading.set(true);

    this.chatAttachments.uploadFromFileList(this.agentId, files).subscribe({
      next: (uploaded) => {
        if (uploaded.length) {
          this.pendingAttachments.update(list => [...list, ...uploaded]);
        }
        this.fileUploading.set(false);
      },
      error: (err) => {
        console.error('File upload failed:', err);
        this.toast.show('File upload failed', 'error');
        this.fileUploading.set(false);
      },
    });
  }

  private uploadFromDataTransfer(dataTransfer: DataTransfer) {
    if (!this.agentId) return;
    this.fileUploading.set(true);

    this.chatAttachments.uploadFromDataTransfer(this.agentId, dataTransfer).subscribe({
      next: (uploaded) => {
        if (uploaded.length) {
          this.pendingAttachments.update(list => [...list, ...uploaded]);
        }
        this.fileUploading.set(false);
      },
      error: (err) => {
        console.error('File upload failed:', err);
        this.toast.show('File upload failed', 'error');
        this.fileUploading.set(false);
      },
    });
  }

  onDrowsyAction(event: { action: 'confirm' | 'deny'; index: number }) {
    const cmd = event.action === 'confirm' ? 'sleep_confirm' : 'sleep_deny';
    const sent = this.ws.sendCommand(cmd);
    if (!sent) {
      this.toast.show(
        'Not connected — could not send sleep response. Reopen chat and try again.',
        'error',
      );
      return;
    }
    this._pendingDrowsyIndex = event.index;
    this.messageList?.markDrowsyResponded(event.index);
  }

  onBudgetAction(event: { action: 'extend' | 'stop'; extraIterations?: number; index: number }) {
    const cmd = event.action === 'stop' ? 'budget_stop' : 'budget_extend';
    const sent = this.ws.sendCommand(cmd, {
      extra_iterations: event.extraIterations ?? 10,
    });
    if (!sent) {
      this.toast.show(
        'Not connected — could not send budget response. Reopen chat and try again.',
        'error',
      );
      return;
    }
    this._pendingBudgetIndex = event.index;
    this.messageList?.markBudgetResponded(event.index);
  }

  ngAfterViewChecked() {
    // Draw waveform when recording
    if (this.voice.state() === 'recording' && this.waveformCanvas) {
      this._drawWaveform();
    }
  }

  toggleSidebar() {
    this.panels.toggleRight('live');
    this.sidebarOpen.set(this.panels.rightDockOpen());
    ChatComponent.persistNeuralSidebarPreference(this.panels.rightDockOpen());
  }

  toggleWorkbenchPanel(): void {
    this.panels.toggleLeft('workbench');
    if (this.panels.leftDock() === 'workbench') {
      this.workbench.openPanel();
    } else {
      this.workbench.closePanel();
    }
  }

  toggleBrowserPanel(): void {
    this.panels.toggleLeft('browser');
  }

  toggleInboxPanel(): void {
    this.panels.toggleRight('inbox');
    this.syncInboxBadge();
  }

  toggleContextPanel(): void {
    this.panels.toggleRight('context');
  }

  toggleFocusMode(): void {
    this.panels.toggleFocusMode();
  }

  openInboxFromNav(): void {
    this.panels.openRight('inbox');
    this.syncInboxBadge();
  }

  onInboxSelect(sessionKey: string): void {
    this.switchThread(sessionKey);
    this.panels.openRight('inbox');
  }

  private syncInboxBadge(): void {
    this.panels.setInboxBadge(this.conversations.inboxBadge());
  }

  private static readNeuralSidebarPreference(): boolean {
    try {
      const stored = localStorage.getItem(ChatComponent.NEURAL_SIDEBAR_KEY);
      if (stored === null) return false;
      return stored === 'true';
    } catch {
      return false;
    }
  }

  private static persistNeuralSidebarPreference(open: boolean): void {
    try {
      localStorage.setItem(ChatComponent.NEURAL_SIDEBAR_KEY, String(open));
    } catch {
      // ignore quota / private browsing
    }
  }

  formatMs(ms: number): string {
    if (!ms || ms <= 0) return '';
    if (ms < 1000) return `${Math.round(ms)}ms`;
    return `${(ms / 1000).toFixed(1)}s`;
  }

  openBottomSheet() {
    this.bottomSheetOpen.set(true);
    this.panels.setMobileSheetTab(this.panels.mobileSheetTab());
  }

  setMobileSheetTab(tab: 'live' | 'inbox' | 'context'): void {
    this.panels.setMobileSheetTab(tab);
  }

  closeBottomSheet() {
    this.bottomSheetOpen.set(false);
  }

  toggleMobileConvNav(): void {
    this.mobileConvNavOpen.update((v) => !v);
  }

  closeMobileConvNav(): void {
    this.mobileConvNavOpen.set(false);
  }

  /** Top hormone name + value for the condensed mobile bar. */
  get topHormone(): { name: string; value: number; color: string } | null {
    const h = this.nlsMetadata()?.hormones;
    if (!h) return null;
    const map: Record<string, { name: string; color: string }> = {
      dopamine: { name: 'Dop', color: 'var(--accent-success)' },
      serotonin: { name: 'Ser', color: 'var(--accent-primary)' },
      norepinephrine: { name: 'Nor', color: 'var(--accent-warn)' },
      cortisol: { name: 'Cor', color: 'var(--accent-danger)' },
      oxytocin: { name: 'Oxy', color: 'var(--accent-primary)' },
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
    ctx.strokeStyle = 'var(--accent-primary)';
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

  /** Activity sidebar = orchestrator (EM) only; delegate streams use run panel + workbench. */
  private _activitySidebarAllowed(): boolean {
    return this._activitySidebarSuppressDepth === 0;
  }

  /**
   * Update the background activity card's detail text, creating it if needed.
   */
  private _updateBackgroundCardDetail(detail: string): void {
    if (!this._activitySidebarAllowed()) return;
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
    const formatted = parseAgentMessageText(text);
    const detailText = detail || formatted.body || '';
    this.activities.update(acts => [{
      id,
      kind: kind as any,
      text: formatted.headline || text,
      detail: detailText,
      tags: [],
      signals: 0,
      factsStored: 0,
      timestamp: new Date(),
      metadata: {
        autonomous: true,
        activityChips: formatted.chips,
        ...metadata,
      },
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

  toggleDelegateExpanded(delegateNumber: number): void {
    this.runView.toggleDelegateExpanded(delegateNumber);
  }

  private hydrateRunFromApi(): void {
    if (!this.agentId) return;
    this.api.getTeams(this.agentId, true).subscribe({
      next: (res) => {
        const teams = res.teams || [];
        if (teams.length) this.runView.hydrateTeams(teams);
        const planId = teams.find((t: { plan_id?: string }) => t.plan_id)?.plan_id;
        if (planId) {
          this.api.getTimeline(this.agentId, planId).subscribe({
            next: (tl) => {
              if (!tl?.waves?.length) return;
              const steps = tl.waves.flatMap((w: { steps?: unknown[] }) => w.steps || []);
              if (steps.length) {
                this.runView.hydratePlan({
                  id: tl.plan_id,
                  title: tl.title || '',
                  status: tl.status || 'in_progress',
                  progress: '',
                  steps,
                });
              }
            },
          });
        }
      },
    });
    this.api.getAgentDelegates(this.agentId).subscribe({
      next: (data) => {
        this.runView.hydrateDelegates(data);
        this._seedChatFromDelegatesApi(data);
      },
      error: () => {},
    });
  }

  private _allRunDelegates(): RunDelegate[] {
    const fromSteps = this.runView.steps().flatMap(s => s.delegates || []);
    const unassigned = this.runView.unassignedDelegates();
    const byNum = new Map<number, RunDelegate>();
    for (const d of [...fromSteps, ...unassigned]) {
      byNum.set(d.number, d);
    }
    return [...byNum.values()];
  }

  private _mapRunDelegateToCard(d: RunDelegate): ChatMessage['delegate'] {
    return {
      number: d.number,
      task: d.task,
      status: d.status === 'queued' ? 'running' : d.status,
      partialTimeout: d.partialTimeout,
      toolCalls: d.toolCalls.map(tc => ({
        name: tc.name,
        args: tc.args,
        callId: tc.callId,
        result: tc.result,
        isError: tc.isError,
      })),
      summary: d.summary,
      iterations: d.iterations,
      totalToolCalls: d.totalToolCalls,
      maxIterations: d.maxIterations,
      expanded: d.expanded,
      teamId: d.teamId,
      waveAttempt: d.waveAttempt,
      teamName: d.teamName,
    };
  }

  private _syncDelegateCardFromRunView(delegateNumber: number): void {
    if (delegateNumber < 0) return;
    const d = this._allRunDelegates().find(x => x.number === delegateNumber);
    if (!d) return;
    const delegate = this._mapRunDelegateToCard(d);
    this.messages.update(msgs => {
      const idx = msgs.findIndex(
        m => m.type === 'delegate_card' && m.delegate?.number === delegateNumber,
      );
      const card: ChatMessage = {
        type: 'delegate_card',
        content: d.task.split('\n')[0],
        delegate,
        timestamp: new Date(),
      };
      if (idx >= 0) {
        const updated = [...msgs];
        updated[idx] = { ...updated[idx], ...card };
        return updated;
      }
      return [...msgs, card];
    });
  }

  private _seedChatFromDelegatesApi(data: {
    batches?: Record<string, { batch_id?: string; total?: number; completed?: number }>;
    delegates?: Array<{ delegate_number: number; batch_id?: string; state?: string; partial?: boolean }>;
    running_count?: number;
  } | null | undefined): void {
    if (!data?.delegates?.length) return;

    const batchEntries = Object.entries(data.batches || {});
    if (batchEntries.length === 1) {
      const [key, batch] = batchEntries[0];
      const batchId = batch.batch_id || key;
      const total = batch.total ?? data.delegates.length;
      const running = data.running_count ?? data.delegates.filter(d => d.state === 'running').length;
      const done = data.delegates.filter(d => d.state !== 'running' && d.state !== 'queued').length;
      const partialCount = data.delegates.filter(d => d.partial).length;
      const complete = running === 0 && done >= total;

      this.messages.update(msgs => {
        if (msgs.some(m => m.type === 'delegate_batch_pill' && m.batchId === batchId)) {
          return msgs;
        }
        let content: string;
        if (complete) {
          content = partialCount > 0
            ? `All ${total} sub-agents finished (${partialCount} partial) — compiling results…`
            : `All ${total} sub-agents completed — compiling results…`;
        } else if (running > 0) {
          content = `${running} sub-agents working in parallel · ${done}/${total} done`;
        } else {
          content = `${done}/${total} sub-agents done`;
        }
        return [...msgs, {
          type: 'delegate_batch_pill' as const,
          content,
          batchId,
          batchCount: total,
          batchRunning: running,
          batchDone: done,
          batchComplete: complete,
          timestamp: new Date(),
        }];
      });
    }

    for (const d of data.delegates) {
      if (d.delegate_number >= 0) {
        this._syncDelegateCardFromRunView(d.delegate_number);
      }
    }
  }

  private _suppressOrchestratorToolInChat(msg: Record<string, unknown>): boolean {
    // Bash stream chunks only — tool chips stay in chat alongside the workbench.
    if (msg['sub_agent'] === true) return false;
    if (msg['autonomous'] === true) return false;
    if (msg['type'] !== 'tool_output_chunk') return false;
    return this.runView.visible();
  }

  private _updateDelegateBatchPill(
    batchId: string,
    _delegateNumber: number,
    outcome: 'ok' | 'partial' | 'failed',
  ): void {
    if (!batchId) return;
    this.messages.update(msgs => msgs.map(m => {
      if (m.type !== 'delegate_batch_pill' || m.batchId !== batchId || m.batchComplete) {
        return m;
      }
      const total = m.batchCount ?? m.batchRunning ?? 0;
      const done = Math.min(total, (m.batchDone ?? 0) + 1);
      const running = Math.max(0, total - done);
      const suffix = outcome === 'failed'
        ? ' (1 failed)'
        : outcome === 'partial'
          ? ' (1 partial)'
          : '';
      return {
        ...m,
        batchDone: done,
        batchRunning: running,
        content: running > 0
          ? `${running} sub-agents working · ${done}/${total} done${suffix}`
          : `All sub-agents finished · ${done}/${total} done${suffix}`,
      };
    }));
  }

  private _completeDelegateBatchPill(batchId: string, count: number): void {
    this.messages.update(msgs => {
      const idx = msgs.findIndex(m => m.type === 'delegate_batch_pill' && m.batchId === batchId);
      if (idx >= 0) {
        const updated = [...msgs];
        updated[idx] = {
          ...updated[idx],
          batchComplete: true,
          batchDone: count,
          batchRunning: 0,
          content: `All ${count} sub-agents completed — compiling results…`,
        };
        return updated;
      }
      return [...msgs, {
        type: 'delegate_batch_pill' as const,
        content: `All ${count} sub-agents completed — compiling results…`,
        batchId,
        batchCount: count,
        batchDone: count,
        batchRunning: 0,
        batchComplete: true,
        timestamp: new Date(),
      }];
    });
  }

  private clearAwaitingResponse(): void {
    this.awaitingResponse.set(false);
    this.generationStopping.set(false);
  }

  /** Insert pre-tool prose before the first tool chip for this agentic step. */
  private _insertAssistantBeforeToolStep(
    step: number,
    content: string,
    reasoning?: string,
  ): void {
    const trimmed = (content || '').trim();
    if (!trimmed) return;
    const assistantMsg = {
      type: 'assistant' as any,
      content: trimmed,
      reasoning: reasoning || undefined,
      timestamp: new Date(),
      agenticStep: step,
    };
    this.messages.update(msgs => {
      const insertIdx = msgs.findIndex(m =>
        m.type === 'tool_progress'
        && (m as any).toolProgress?.iteration === step,
      );
      if (insertIdx >= 0) {
        const updated = [...msgs];
        updated.splice(insertIdx, 0, assistantMsg);
        return updated;
      }
      return [...msgs, assistantMsg];
    });
  }

  /** Commit pre-tool prose before the tool chip appears (avoids hold_prose loss). */
  private _flushPendingIterProse(iteration: number): void {
    const raw = this._pendingIterText.trim();
    if (!raw) return;
    const thought = parseThinking(raw);
    const content = (thought.response || raw).trim();
    if (!content) {
      this._pendingIterText = '';
      return;
    }
    const reasoning = this._preToolReasoning || thought.thinking || undefined;
    this._insertAssistantBeforeToolStep(iteration, content, reasoning);
    this._pendingIterText = '';
    this._iterTextCommitted = true;
  }

  stopGeneration(): void {
    if (this.agenticActive()) {
      this.cancelAgentic();
      return;
    }
    this.generationStopping.set(true);
    this.ws.sendAbort();
  }

  private handleRuntimeMessage(msg: any) {
    if (!msg._wbDone) {
      msg._wbDone = true;
      this.workbench.recordFromRuntime(msg);
    }
    this.runView.handleMessage(msg);
    const suppressActivitySidebar = msg.sub_agent === true;
    if (suppressActivitySidebar) {
      this._activitySidebarSuppressDepth++;
    }
    switch (msg.type) {
      case 'history':
        // Restore conversation history from runtime on connect
        if (this.agenticActive()) {
          // Live agentic UI (ask_user cards, in-flight steps) must not be
          // wiped by transcript replay on reconnect mid-task.
          break;
        }
        if (Array.isArray(msg.messages) && msg.messages.length > 0) {
          const restored = restoreChatMessagesFromTranscript(msg.messages, {
            onPlanHydrate: (meta) => {
              const planSteps = meta['plan_steps'];
              if (!Array.isArray(planSteps) || planSteps.length === 0) {
                return;
              }
              this.runView.hydratePlan({
                id: String(meta['plan_id'] || ''),
                title: String(meta['plan_title'] || 'Restored plan'),
                status: 'in_progress',
                progress: '',
                steps: planSteps.map((s: any, idx: number) => ({
                  id: s.id || `step-${idx + 1}`,
                  label: typeof s === 'string' ? s : (s.label || ''),
                  status: typeof s === 'string' ? 'pending' : (s.status || 'pending'),
                  depends_on: [],
                  delegatable: true,
                })),
              });
            },
          });
          this.messages.set(restored);
          this.syncMainTranscript();
        }
        break;

      case 'token':
        this.clearAwaitingResponse();
        this.streamingText.update(t => t + msg.content);
        break;

      case 'reasoning_token':
        this.clearAwaitingResponse();
        this.streamingReasoning.update(t => t + (msg.content || ''));
        break;

      case 'reasoning_end':
        break;

      case 'response_replace':
        this.clearAwaitingResponse();
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
        this.clearAwaitingResponse();
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
              sleep_count: msg.nls?.sleep_count ?? existing.sleep_count ?? 0,
              ans: msg.nls?.ans ?? existing.ans ?? {},
              turn_count: (existing.turn_count ?? 0) + 1,
              facts_in_memory: msg.nls?.facts_in_memory ?? existing.facts_in_memory ?? 0,
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
          if (msg.agentic_running !== true) {
            this.clearStaleAgenticUi('reconnect');
          }
        }

        if (statusText === 'sleeping') {
          this.clearAwaitingResponse();
          const reason = msg.sleep_reason || 'consolidating memories';
          this.messages.update(msgs => [...msgs, {
            type: 'status',
            content: `Agent is sleeping (${reason})...`,
            timestamp: new Date(),
          }]);
        } else if (statusText === 'alive' && msg.sleep_complete) {
          // Wake chat bubble handled in case 'sleep_complete'
        } else if (statusText !== 'alive') {
          // Show other status messages (errors, custom content)
          this.clearAwaitingResponse();
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
        if (msg.facts_in_memory != null) {
          this.nlsMetadata.update(m => ({
            ...m,
            facts_in_memory: msg.facts_in_memory,
          }));
        } else if (msg.facts_stored) {
          this.nlsMetadata.update(m => {
            const base = m?.facts_in_memory || 0;
            if (base <= 0) return m ?? {};
            return {
              ...m,
              facts_in_memory: base + (msg.facts_stored || 0),
            };
          });
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
          text: this.channelEventActivityText(msg),
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

      case 'channel_send_result': {
        if (!msg.ok) {
          this.toast.show(msg.error || 'Failed to send on channel', 'error');
          this.messages.update(msgs => {
            const sk = msg.session_key as string;
            const reversed = [...msgs];
            for (let i = reversed.length - 1; i >= 0; i--) {
              const m = reversed[i] as { type?: string; sessionKey?: string; content?: string };
              if (m.type === 'channel_outbound' && m.sessionKey === sk) {
                reversed.splice(i, 1);
                break;
              }
            }
            return reversed;
          });
        }
        break;
      }

      case 'drowsy': {
        // Agent is drowsy and requesting permission to sleep.
        // Show as an amber-bordered bubble with action buttons.
        this.nlsMetadata.update(m => ({
          ...m,
          ans: { ...(m?.ans || {}), state: 'drowsy' },
        }));
        this.activities.update(acts => [{
          id: Date.now(), kind: 'drowsy' as ActivityKind,
          text: msg.content || 'Agent is feeling drowsy…',
          tags: [], signals: 0, factsStored: 0, timestamp: new Date(),
        }, ...acts].slice(0, 15));
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

      case 'turn_triage': {
        this.orchProfiles.noteTriageProfile(this.agentId, {
          profile: msg.profile as string | undefined,
          requested: msg.profile_requested as string | undefined,
          effective: msg.profile_effective as string | undefined,
          floored: msg.profile_floored === true,
        });
        break;
      }

      case 'agentic_start': {
        if (msg.orchestration_profile) {
          this.orchProfiles.noteTriageProfile(this.agentId, {
            profile: msg.orchestration_profile as string,
          });
        }
        if (msg.sub_agent === true) break;
        this.clearAwaitingResponse();

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
        this._agenticStepEvents = [];
        this._preToolReasoning = '';
        this._iterTextCommitted = false;
        this._pendingIterText = '';
        this._toolTurnActive = false;
        this.messages.update(msgs => [...msgs, {
          type: 'agentic_start' as any,
          content: `Agent starting task (up to ${msg.max_steps || 15} steps)`,
          timestamp: new Date(),
        }]);
        this.panels.onAgenticStart();
        this.workbench.openPanel();
        break;
      }

      case 'agentic_iteration': {
        if (msg.sub_agent === true) break;
        this.clearAwaitingResponse();

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
        const holdProse = msg.hold_prose === true;
        const iterText = holdProse ? '' : (this._pendingIterText || this.streamingText());
        const iterReasoning = holdProse ? '' : this.streamingReasoning();
        this._pendingIterText = '';

        if (holdProse) {
          this.streamingText.set('');
          this.streamingReasoning.set('');
        } else if (iterText && !this._iterTextCommitted) {
          const thought = parseThinking(iterText);
          const content = thought.response || iterText;
          const reasoning = (iterReasoning || thought.thinking) || undefined;
          if (toolCalls.length > 0) {
            this._insertAssistantBeforeToolStep(step, content, reasoning);
          } else {
            this.messages.update(msgs => [...msgs, {
              type: 'assistant' as any,
              content,
              reasoning,
              timestamp: new Date(),
              agenticStep: step,
            }]);
          }
          this._iterTextCommitted = true;
        } else if (iterReasoning) {
          this._preToolReasoning = iterReasoning;
        }
        this.streamingText.set('');
        this.streamingReasoning.set('');
        this._toolTurnActive = false;

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
          const silentYield = isSilentAutonomousCompletion(msg);
          const bgSteps = msg.total_steps || 0;
          const bgTools = msg.total_tool_calls || 0;
          const bgDur = ((msg.duration_ms || 0) / 1000).toFixed(0);
          const bgLabel = bgAborted
            ? agenticAbortLabel(true, msg.abort_reason, true)
            : `Task completed (${bgSteps} steps, ${bgTools} tool calls, ${bgDur}s)`;

          if (!silentYield) {
            // Ensure activity card exists (fallback if agentic_start was missed)
            if (!this._backgroundTaskId || !this.activities().some(a => a.id === this._backgroundTaskId)) {
              this._backgroundTaskId = Date.now();
              this.activities.update(acts => [{
                id: this._backgroundTaskId, kind: 'todo' as any,
                text: bgAborted ? 'Check-in cancelled' : 'Task completed',
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
          }
          // Skip chat noise for internal orchestration yields (delegates still run)
          const bgStatus = silentYield
            ? ''
            : bgAborted
              ? agenticAbortLabel(true, msg.abort_reason, true)
              : `Background task completed (${bgSteps} steps, ${bgDur}s)`;
          if (bgStatus) {
            this.messages.update(msgs => [...msgs, {
              type: 'status' as any,
              content: bgStatus,
              timestamp: new Date(),
            }]);
          }
          sessionStorage.removeItem(`bg_task_${this.agentId}`);
          break;
        }

        // Foreground task: show full details in chat
        this.clearAwaitingResponse();
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
        this.budgetPromptPending.set(false);
        this.activityStatus.set('');
        this.agenticStep.set(0);
        this.streamingText.set('');
        this.streamingReasoning.set('');
        this._pendingIterText = '';
        this._toolTurnActive = false;

        const exitReason = String(
          msg.exit_reason || msg.abort_reason || '',
        ).trim();
        const silentYield = isSilentOrchestrationExit(exitReason);
        const aborted = (msg.aborted || false) && !silentYield;
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
          content: agenticAbortLabel(aborted, exitReason, false),
          timestamp: new Date(),
          agenticComplete: {
            totalSteps,
            totalToolCalls,
            aborted,
            abortReason: exitReason,
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
          const buffered = filterNewLearnTags(
            [...this._bufferedLearnings],
            this._seenLearningKeys,
          );
          this._bufferedLearnings = [];
          if (buffered.length > 0) {
            setTimeout(() => this._appendSignalTags(buffered), 100);
          }
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
        const statusType = msg.status || '';
        if (msg.autonomous) {
          this._updateBackgroundCardDetail(elapsed ? `${text} (${(elapsed / 1000).toFixed(1)}s)` : text);
          break;
        }
        if (statusType === 'waiting_for_budget' || /waiting for your decision/i.test(text)) {
          this.budgetPromptPending.set(true);
          this.askUserPending.set(false);
        } else if (statusType === 'waiting_for_user' || /waiting for your answer/i.test(text)) {
          this.askUserPending.set(true);
          this.budgetPromptPending.set(false);
        }
        if (!text.trim()) {
          if (statusType !== 'generating' || !this.agenticActive()) {
            this.activityStatus.set('');
          }
          break;
        }
        if (elapsed && text) {
          this.activityStatus.set(`${text} (${(elapsed / 1000).toFixed(1)}s)`);
        } else {
          this.activityStatus.set(text);
        }
        break;
      }

      case 'project_processes_changed': {
        if (Array.isArray(msg.processes)) {
          this.projectProcesses.set(msg.processes);
        } else {
          void this.refreshProjectProcesses();
        }
        break;
      }

      case 'delegate_batch_started': {
        const batchId = msg.batch_id || '';
        const count = msg.count || 0;
        if (!this.runView.expanded()) {
          this.runView.setExpanded(true);
        }
        this.messages.update(msgs => {
          if (msgs.some(m => m.type === 'delegate_batch_pill' && m.batchId === batchId)) {
            return msgs;
          }
          return [...msgs, {
            type: 'delegate_batch_pill' as const,
            content: `${count} sub-agents working in parallel`,
            batchId,
            batchCount: count,
            batchRunning: count,
            batchDone: 0,
            timestamp: new Date(),
          }];
        });
        this.activityStatus.set(`${count} sub-agents launched`);
        break;
      }

      case 'delegate_start': {
        const dlgTask = msg.delegate_task || 'Sub-task';
        if (!msg.batch_id) {
          this.activityStatus.set(`Delegating: ${dlgTask.slice(0, 80)}`);
        }
        if (!this.runView.expanded()) {
          this.runView.setExpanded(true);
        }
        const dNum = msg.delegate_number;
        if (typeof dNum === 'number' && dNum >= 0) {
          this._syncDelegateCardFromRunView(dNum);
        }
        break;
      }

      case 'delegate_end': {
        const batchId = msg.batch_id as string | undefined;
        const dNum = msg.delegate_number;
        if (batchId && typeof dNum === 'number') {
          const outcome = msg.partial ? 'partial' : (msg.aborted ? 'failed' : 'ok');
          this._updateDelegateBatchPill(batchId, dNum, outcome);
        }
        if (typeof dNum === 'number' && dNum >= 0) {
          this._syncDelegateCardFromRunView(dNum);
        }
        if (this.runView.runningDelegateCount() === 0) {
          this.activityStatus.set('');
        }
        break;
      }

      case 'delegate_batch_complete': {
        const count = msg.count || 0;
        const batchId = msg.batch_id || '';
        this._completeDelegateBatchPill(batchId, count);
        this.activityStatus.set('Compiling delegate results...');
        break;
      }

      case 'team_created':
      case 'team_advanced': {
        const team = msg.team;
        if (team?.status === 'created') {
          const name = team.name || team.id || 'Wave';
          this.activities.update(acts => [{
            id: Date.now(),
            kind: 'todo' as ActivityKind,
            text: 'Wave planned — not launched',
            detail: `${name} [${team.id}]`,
            tags: labelTags(['Team', 'Not launched']),
            signals: 0,
            factsStored: 0,
            timestamp: new Date(),
            metadata: { team_id: team.id, status: 'created' },
          }, ...acts].slice(0, 20));
        }
        break;
      }

      case 'team_launched': {
        const team = msg.team;
        if (team?.id) {
          this.activities.update(acts => acts.filter(
            a => !(a.metadata?.team_id === team.id && a.metadata?.status === 'created'),
          ));
        }
        break;
      }

      case 'delegate_progress':
        break;

      case 'tool_output_chunk': {
        const chunk = msg.chunk || '';
        const toolName = msg.tool_name || 'bash';
        if (msg.sub_agent === true) {
          break;
        }
        if (this._suppressOrchestratorToolInChat(msg)) {
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
        break;
      }

      case 'agentic_token': {
        if (msg.sub_agent === true || msg.autonomous) break;
        this.clearAwaitingResponse();
        if (msg.thinking) {
          this.streamingReasoning.update(t => t + (msg.token || ''));
        } else if (this._toolTurnActive) {
          this._pendingIterText += msg.token || '';
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
          this._toolTurnActive = true;
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

        // For write tools, show file content streaming (skip when run panel owns orchestration UI)
        if (
          (acc.name === 'write' || acc.name === 'write_file' || acc.name === 'create_file')
          && !this.runView.visible()
        ) {
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
        this.clearAwaitingResponse();
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
        if (msg.project_dir) {
          this.workspaceCtx.setProjectDir(this.agentId, String(msg.project_dir));
        }
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
            this._updateBackgroundCardDetail(
              `Plan (${labels.length} steps): ${labels.slice(0, 3).join(', ')}${labels.length > 3 ? '…' : ''}`,
            );

            // Track plan steps for step-completed cards
            this._bgPlanSteps = labels;
            this._bgPlanStatuses = statuses;
          }
          break;
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
            this._updateBackgroundCardDetail(
              `Step ${bgStepIdx + 1} done${stepLabel ? ': ' + stepLabel : ''}`,
            );
          }
          break;
        }
        break;
      }

      case 'tool_execution_start': {
        const toolName = msg.tool_name || '';
        const args = msg.arguments || {};
        if (!msg.autonomous) {
          this.clearAwaitingResponse();
        }

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
          } else if (toolName === 'switch_mode') {
            const parsed = toolWorkbenchTitle('switch_mode', args as Record<string, unknown>);
            bgLabel = parsed.title;
          } else {
            bgLabel = `Running ${toolName}...`;
          }
          this._updateBackgroundCardDetail(bgLabel);
          break;
        }
        const callId = msg.call_id || '';
        const subAgent = msg.sub_agent === true;
        const dlgNum: number = msg.delegate_number ?? -1;

        if (subAgent && dlgNum >= 0) {
          break;
        }

        this._flushPendingIterProse(msg.iteration || 0);

        const normArgs = normalizeToolArguments(args);
        let filePaths: string[] = [];
        let label = toolName;
        if (toolName === 'plan') {
          this.workspaceCtx.noteProjectDirFromText(
            this.agentId,
            JSON.stringify(normArgs),
          );
        }
        if (toolName === 'write' || toolName === 'write_file' || toolName === 'create_file') {
          filePaths = this._enrichFilePaths(collectFilePaths(toolName, normArgs));
          const name = filePaths[0] ? fileDisplayName(filePaths[0]) : 'file';
          label = `Writing ${name}…`;
        } else if (toolName === 'bash') {
          label = normArgs['command']
            ? `$ ${normArgs['command']}`
            : 'Running command...';
        } else if (toolName === 'read' || toolName === 'read_file') {
          filePaths = this._enrichFilePaths(collectFilePaths(toolName, normArgs));
          const name = filePaths[0] ? fileDisplayName(filePaths[0]) : 'file';
          label = `Reading ${name}…`;
        } else if (toolName === 'edit') {
          filePaths = this._enrichFilePaths(collectFilePaths(toolName, normArgs));
          const name = filePaths[0] ? fileDisplayName(filePaths[0]) : 'file';
          label = `Editing ${name}…`;
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

        // Pre-tool prose is committed at agentic_iteration (turn_end) so it
        // lands before tool chips and respects hold_prose from the backend.
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
                toolProgress: {
                  ...m.toolProgress,
                  callId,
                  arguments: normArgs,
                  ...(filePaths.length ? { filePaths } : {}),
                },
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
              arguments: normArgs,
              ...(filePaths.length ? { filePaths } : {}),
              done: false,
              iteration: msg.iteration || 0,
            },
            timestamp: new Date(),
          }];
        });

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
        const details = msg.details || null;
        const preview = msg.result_preview || '';
        const outcome = resolveToolDisplayOutcome(
          !!msg.is_error,
          preview,
          toolName,
        );
        const isError = outcome === 'error';
        const isWarning = outcome === 'warn';
        const subAgent = msg.sub_agent === true;
        const dlgNum: number = msg.delegate_number ?? -1;

        if (subAgent && dlgNum >= 0) {
          break;
        }

        let doneLabel = toolDoneLabel(toolName, outcome);
        if (toolName === 'bash' && !isError && details?.daemon && details?.pid) {
          void this.refreshProjectProcesses();
        }

        const endArgs = normalizeToolArguments(msg.arguments || {});
        if (toolName === 'plan') {
          this.workspaceCtx.noteProjectDirFromText(
            this.agentId,
            msg.result_preview || '',
          );
        }
        const endFilePaths = this._enrichFilePaths(
          collectFilePaths(
            toolName,
            endArgs,
            msg.result_preview || '',
          ),
        );

        this.messages.update(msgs => {
          const updated = [...msgs];
          let matchIdx = -1;
          for (let i = updated.length - 1; i >= 0; i--) {
            const m = updated[i] as any;
            if (m.type === 'tool_progress' && m.toolProgress?.callId === callId) {
              const mergedPaths = endFilePaths.length
                ? endFilePaths
                : (m.toolProgress?.filePaths || []);
              updated[i] = {
                ...m,
                content: doneLabel,
                toolProgress: {
                  ...m.toolProgress,
                  arguments: { ...m.toolProgress?.arguments, ...endArgs },
                  done: true,
                  isError,
                  isWarning,
                  ...(mergedPaths.length ? { filePaths: mergedPaths } : {}),
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
                const newItems = [
                  ...existingItems,
                  { label: doneLabel, isError, isWarning },
                ];
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
                    isWarning: newItems.some(
                      (it: any) => it.isWarning && !it.isError,
                    ),
                  },
                };
                updated.splice(matchIdx, 1);
              }
            }
          }

          return updated;
        });

        this.activityStatus.set('');
        break;
      }

      case 'browser_command': {
        const action = msg.action || 'navigate';
        const url = msg.url || '';
        const reqId = msg.request_id || '';
        console.log(`[CHAT] Received browser_command: action=${action} url=${url} reqId=${reqId}`);
        this.browserCommand.set({ ...msg });
        // Keep the in-app webview in the DOM but do not expand the sidebar —
        // browsing runs in the background unless the user opens the panel.

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
        this.clearAwaitingResponse();
        this.messages.update(msgs => [...msgs, {
          type: 'copilot_ack' as any,
          content: msg.message || 'Guidance received.',
          timestamp: new Date(),
        }]);
        break;
      }

      case 'ask_user': {
        this.clearAwaitingResponse();
        this.askUserPending.set(true);
        this.budgetPromptPending.set(false);
        this.activityStatus.set('Waiting for your answer…');
        const question = msg.question || 'I need more information to continue.';
        this.messages.update(msgs => [...msgs, {
          type: 'ask_user',
          content: question,
          requestId: msg.request_id || '',
          timestamp: new Date(),
        }]);
        if (typeof msg.source === 'string' && msg.source.startsWith('user:channel')) {
          const sk = msg.session_key || msg.sessionKey || this.currentThread();
          this.conversations.addInboxItem({
            sessionKey: sk,
            kind: 'ask_user',
            preview: question.slice(0, 120),
            channel: sk.split(':')[0] || 'channel',
            timestamp: new Date(),
            priority: 100,
          });
          this.panels.onAskUserFromChannel();
          this.syncInboxBadge();
        }
        this.toast.show(
          question.length > 200 ? `${question.slice(0, 197)}…` : question,
          'info',
          0,
        );
        break;
      }

      case 'loop_budget_prompt': {
        this.clearAwaitingResponse();
        this.budgetPromptPending.set(true);
        this.askUserPending.set(false);
        this.activityStatus.set('Waiting for your decision…');
        const question = msg.question || 'I need more steps to continue.';
        const options = Array.isArray(msg.options)
          ? msg.options.map((o: unknown) => Number(o)).filter((o: number) => o > 0)
          : [10, 20, 40];
        this.messages.update(msgs => [...msgs, {
          type: 'loop_budget_prompt' as any,
          content: question,
          requestId: msg.request_id || '',
          sessionKey: msg.session_key || msg.sessionKey || this.currentThread(),
          timestamp: new Date(),
          loopBudget: {
            reason: msg.reason || '',
            requestId: msg.request_id || '',
            iteration: msg.iteration || 0,
            maxIterations: msg.max_iterations || 0,
            options: options.length ? options : [10, 20, 40],
            sessionKey: msg.session_key || msg.sessionKey || this.currentThread(),
            waitSeconds: msg.wait_seconds || 600,
          },
        }]);
        if (typeof msg.source === 'string' && msg.source.startsWith('user:channel')) {
          const sk = msg.session_key || msg.sessionKey || this.currentThread();
          this.conversations.addInboxItem({
            sessionKey: sk,
            kind: 'ask_user',
            preview: question.slice(0, 120),
            channel: sk.split(':')[0] || 'channel',
            timestamp: new Date(),
            priority: 100,
          });
          this.panels.onAskUserFromChannel();
          this.syncInboxBadge();
        }
        this.toast.show(
          question.length > 200 ? `${question.slice(0, 197)}…` : question,
          'info',
          0,
        );
        break;
      }

      case 'budget_decision': {
        this.budgetPromptPending.set(false);
        this.activityStatus.set('');
        this.toast.dismissAll();
        if (msg.action === 'extend') {
          const newMax = Number(msg.max_iterations) || 0;
          if (newMax > 0) {
            this.agenticMaxSteps.set(newMax);
          } else if (msg.extra_iterations) {
            this.agenticMaxSteps.update(v => v + Number(msg.extra_iterations));
          }
          this.messages.update(msgs => [...msgs, {
            type: 'status' as any,
            content: `Continuing with +${msg.extra_iterations || 0} steps (up to ${this.agenticMaxSteps()} total)…`,
            timestamp: new Date(),
          }]);
        }
        break;
      }

      case 'budget_command_result': {
        const pending = this._pendingBudgetIndex;
        if (!msg.ok) {
          if (pending != null) {
            this.messageList?.clearBudgetResponded(pending);
            this._pendingBudgetIndex = null;
          }
          this.toast.show(msg.content || 'Budget response failed.', 'error');
        }
        break;
      }

      case 'communicate': {
        this.streamingText.set('');
        // Auto-surfaced turn text stays in workbench; explicit communicate() + milestones show here.
        const hideAutonomousChatter =
          (msg.mid_loop || msg.autonomous)
          && !isUserFacingOrchestrationMessage(msg);
        if (hideAutonomousChatter) {
          break;
        }
        this.clearAwaitingResponse();
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
        this.activityStatus.set('');
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
        const allTags = filterNewLearnTags(
          [...facts.map(f => `[LEARN:${f}]`), ...emotionTags],
          this._seenLearningKeys,
        );
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

      case 'sleep_start': {
        const sleepReason = msg.sleep_reason || msg.reason || 'consolidating memories';
        this.messages.update(msgs => msgs.filter(m => m.type !== 'drowsy'));
        this.nlsMetadata.update(m => ({
          ...m,
          ans: { ...(m?.ans || {}), state: 'sleeping' },
        }));
        this.activities.update(acts => [{
          id: Date.now(), kind: 'sleep_start' as ActivityKind,
          text: sleepReason, tags: [],
          signals: 0, factsStored: 0, timestamp: new Date(),
        }, ...acts].slice(0, 15));
        break;
      }

      case 'sleep_command_result': {
        const pending = this._pendingDrowsyIndex;
        if (!msg.ok) {
          if (pending != null) {
            this.messageList?.clearDrowsyResponded(pending);
            this._pendingDrowsyIndex = null;
          }
          this.toast.show(msg.content || 'Sleep response could not be applied.', 'error');
          break;
        }
        this._pendingDrowsyIndex = null;
        if (msg.action === 'deny') {
          this.messages.update(msgs => [...msgs, {
            type: 'status',
            content: 'You said: "Stay awake." Agent will keep going.',
            timestamp: new Date(),
          }]);
          this.messages.update(msgs => msgs.filter(m => m.type !== 'drowsy'));
        }
        // confirm: status(agent_status=sleeping) already added the chat bubble
        break;
      }

      case 'sleep_complete': {
        const nls = msg.nls || {};
        const signals = msg.signals_processed ?? nls.signals_processed ?? 0;
        const sleepCycles = msg.sleep_count ?? nls.sleep_count ?? this.nlsMetadata()?.sleep_count ?? 0;
        this.messages.update(msgs => {
          const withoutSleeping = msgs.filter(m =>
            !(m.type === 'status' && typeof m.content === 'string' &&
              /sleeping/i.test(m.content)),
          );
          return [...withoutSleeping, {
            type: 'status',
            content: `Agent is back up (${signals} signals consolidated, sleep cycle #${sleepCycles}).`,
            timestamp: new Date(),
          }];
        });
        this.nlsMetadata.update(m => ({
          ...m,
          ...nls,
          ans: msg.ans ?? nls.ans ?? m?.ans ?? {},
          hormones: msg.hormones ?? nls.hormones ?? m?.hormones ?? {},
          heartbeat: this._mergeHeartbeat(m?.heartbeat, msg.heartbeat ?? nls.heartbeat),
          facts_in_memory: msg.facts_in_memory ?? nls.facts_in_memory ?? m?.facts_in_memory ?? 0,
          sleep_count: sleepCycles,
          turn_count: msg.turn_count ?? nls.turn_count ?? m?.turn_count ?? 0,
          working_memory: msg.working_memory ?? nls.working_memory ?? m?.working_memory,
          narrative: msg.narrative ?? nls.narrative ?? m?.narrative,
          theory_of_mind: msg.theory_of_mind ?? nls.theory_of_mind ?? m?.theory_of_mind,
          predictive_processing: msg.predictive_processing ?? nls.predictive_processing ?? m?.predictive_processing,
          network_dynamics: msg.network_dynamics ?? nls.network_dynamics ?? m?.network_dynamics,
        }));
        this.activities.update(acts => [{
          id: Date.now(), kind: 'sleep_complete' as ActivityKind,
          text: `Sleep complete — ${signals} signals`, tags: [],
          signals, factsStored: 0, timestamp: new Date(),
        }, ...acts].slice(0, 15));
        break;
      }

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
    if (suppressActivitySidebar) {
      this._activitySidebarSuppressDepth--;
    }
  }

  // ─── In-app browser ───────────────────────────────────────

  private _appendLearnTags(facts: string[]): void {
    this._appendSignalTags(facts.map(f => `[LEARN:${f}]`));
  }

  private _appendSignalTags(tags: string[]): void {
    tags = filterNewLearnTags(tags, this._seenLearningKeys);
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
    this.panels.onBrowserNavigate();
  }

  onOpenUrl(url: string): void {
    this.browserCommand.set({
      type: 'browser_command',
      action: 'navigate',
      url,
      request_id: '',
    });
    this.panels.onBrowserNavigate();
  }

  collapseBrowser(): void {
    if (this.panels.leftDock() === 'browser') {
      this.panels.closeLeft();
    }
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

  switchThread(key: string): void {
    this.currentThread.set(key);
    this.conversations.markThreadRead(key);
    this.syncInboxBadge();
    const meta = this.conversations.threads().find(t => t.key === key);
    const isSurface = !!meta && meta.channel !== 'websocket';
    this.panels.maybeOpenContextForSurface(isSurface, key);
    this.loadThreadHistory(key);
  }

  createNewThread(): void {
    const id = Date.now().toString(36);
    const key = `websocket:thread:${id}`;
    const count = this.conversations.threads().filter(t => t.channel === 'websocket').length;
    const label = `Branch ${count}`;
    this.conversations.addBranch(label, key);
    this.switchThread(key);
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
        const restored: ConversationThread[] = [];
        for (const [key, meta] of Object.entries(sessions) as [string, any][]) {
          if (key === 'websocket:main') continue;
          const channel = meta?.channel || key.split(':')[0] || 'websocket';
          if (channel === 'team' || channel === 'delegate') continue;
          const sender = meta?.sender || '';
          const subject = meta?.subject || '';
          const channelName = meta?.channel_name || meta?.channelName || '';
          const guildName = meta?.guild_name || meta?.guildName || '';
          const flags = this.conversations.threadFlagsFromKey(key);
          const label = this.conversations.labelFromSessionKey(key, channel, {
            sender, subject, channel_name: channelName, guild_name: guildName,
          });
          restored.push({
            key, label, channel, sender, subject,
            channelName: channelName || undefined,
            guildName: guildName || undefined,
            isGroup: flags.isGroup,
          });
        }
        this.conversations.resetThreadsForAgent(this.agentId, restored);
        const current = this.currentThread();
        if (!this.conversations.threads().some((t) => t.key === current)) {
          this.currentThread.set('websocket:main');
        }
      },
      error: () => {},
    });
  }

  private loadThreadHistory(sessionKey: string): void {
    if (sessionKey === 'websocket:main') return;
    if (!this.agentId) return;

    const url = this.platform.isElectron
      ? `${(window as any).nls?.runtimeUrl || 'http://127.0.0.1:9222'}/sessions/${this.agentId}/${encodeURIComponent(sessionKey)}`
      : `${this.api.apiBase}/agents/${this.agentId}/sessions/${encodeURIComponent(sessionKey)}`;

    const threadMeta = this.conversations.threads().find(t => t.key === sessionKey);
    const isChannel = threadMeta && threadMeta.channel !== 'websocket';

    this.http.get<any>(url).subscribe({
      next: (res) => {
        const ambient = Array.isArray(res?.ambient_timeline) ? res.ambient_timeline : [];
        const sessionMsgs = Array.isArray(res?.messages) ? res.messages : [];
        if (!ambient.length && !sessionMsgs.length) return;

        let restored: ChatMessage[];
        if (isChannel && ambient.length > 0) {
          restored = ambient.map((row: any) => {
            const triggered = row.triggered === true;
            const isAssistant = row.role === 'assistant';
            let type: ChatMessage['type'];
            if (isAssistant) {
              type = 'channel_outbound';
            } else if (triggered) {
              type = 'channel_inbound';
            } else {
              type = 'channel_ambient';
            }
            return {
              type,
              content: row.content || '',
              channel: threadMeta!.channel,
              sender: row.sender || threadMeta!.sender || threadMeta!.channel,
              subject: threadMeta!.subject || '',
              sessionKey,
              timestamp: row.timestamp ? new Date(row.timestamp) : new Date(),
            };
          });
        } else if (isChannel) {
          restored = sessionMsgs.map((m: any) => ({
            type: m.role === 'user' ? 'channel_inbound' as any : 'channel_outbound' as any,
            content: m.content || '',
            channel: threadMeta!.channel,
            sender: threadMeta!.sender || threadMeta!.channel,
            subject: threadMeta!.subject || '',
            sessionKey,
            timestamp: new Date(m.timestamp || Date.now()),
          }));
        } else {
          restored = restoreChatMessagesFromTranscript(sessionMsgs, {
            sessionKey,
          });
        }

        this.messages.update(msgs => {
          const other = msgs.filter(m => m.sessionKey !== sessionKey);
          const existing = msgs.filter(m => m.sessionKey === sessionKey);
          // Server history is authoritative for channel threads (ambient or session-backed).
          if (isChannel && restored.length > 0) {
            return [...other, ...restored];
          }
          if (existing.length > 0) return msgs;
          return [...other, ...restored];
        });
      },
      error: () => {},
    });
  }

  /** Called when a channel_event arrives via WebSocket */
  private channelEventActivityText(event: {
    direction?: string;
    sender?: string;
    channel?: string;
    subject?: string;
  }): string {
    const who = event.sender || event.channel || 'channel';
    switch (event.direction) {
      case 'inbound':
        return event.subject ? `Email from ${who}: ${event.subject}` : `Received from ${who}`;
      case 'response':
        return event.subject ? `Replied on email: ${event.subject}` : `Replied on ${event.channel || 'channel'}`;
      case 'ambient':
        return `Group activity from ${who}`;
      case 'skipped':
        return `Skipped message from ${who}`;
      default:
        return `${event.direction || 'Channel event'} · ${who}`;
    }
  }

  handleChannelEvent(event: any): void {
    const channel = event.channel || '';
    const sessionKey = event.session_key || '';
    if (!sessionKey) return;

    const existing = this.conversations.threads().find(t => t.key === sessionKey);
    let threadLabel = existing?.label || '';
    const flags = this.conversations.threadFlagsFromKey(sessionKey);
    const channelName = event.channel_name || event.channelName || '';
    const guildName = event.guild_name || event.guildName || '';
    if (!existing) {
      threadLabel = this.conversations.buildThreadLabel(channel, {
        ...event,
        session_key: sessionKey,
        channel_name: channelName,
        guild_name: guildName,
      });
      this.conversations.upsertThread({
        key: sessionKey,
        label: threadLabel,
        channel,
        sender: event.sender || '',
        subject: event.subject || '',
        channelName: channelName || undefined,
        guildName: guildName || undefined,
        isGroup: flags.isGroup,
      });
    } else if (
      (channelName || guildName)
      && (
        !existing.channelName
        || !existing.guildName
        || this.conversations.threadLabelLooksLikeId(existing)
      )
    ) {
      this.conversations.upsertThread({
        ...existing,
        channelName: channelName || existing.channelName,
        guildName: guildName || existing.guildName,
        isGroup: flags.isGroup,
        label: this.conversations.labelFromSessionKey(sessionKey, channel, {
          sender: existing.sender || event.sender,
          subject: existing.subject || event.subject,
          channel_name: channelName || existing.channelName,
          guild_name: guildName || existing.guildName,
        }),
      });
      threadLabel = this.conversations.threads().find(t => t.key === sessionKey)?.label || threadLabel;
    } else if (!existing.subject && event.subject) {
      this.conversations.upsertThread({
        ...existing,
        subject: event.subject,
        sender: event.sender || existing.sender,
      });
    }

    const sender = event.sender || channel;
    const direction = event.direction || 'inbound';
    const inboundContent = event.content || event.content_preview || '';

    if (direction === 'skipped') {
      const reason = event.skip_reason || 'Policy blocked this message';
      this.conversations.addInboxItem({
        sessionKey,
        kind: 'skipped',
        preview: reason,
        sender,
        channel,
        conversationLabel: threadLabel,
        timestamp: new Date(),
        priority: 30,
      });
      this.panels.suggestInbox();
      this.syncInboxBadge();
      this.messages.update(msgs => [...msgs, {
        type: 'channel_skipped',
        content: reason,
        channel,
        sender,
        subject: event.subject || '',
        sessionKey,
        timestamp: new Date(),
      }]);
      return;
    }

    if (direction === 'ambient' && inboundContent) {
      this.messages.update(msgs => {
        const alreadyShown = msgs.some(m =>
          m.type === 'channel_ambient'
          && (m as any).sessionKey === sessionKey
          && m.content === inboundContent
        );
        if (alreadyShown) return msgs;
        return [...msgs, {
          type: 'channel_ambient',
          content: inboundContent,
          channel,
          sender,
          subject: event.subject || '',
          sessionKey,
          timestamp: new Date(),
        }];
      });
      return;
    }

    // Only show the inbound bubble on the "inbound" direction event.
    // The later "response" event re-sends the same content field —
    // we must not create a second bubble from it.
    if (inboundContent && direction === 'inbound') {
      this.conversations.addInboxItem({
        sessionKey,
        kind: 'inbound',
        preview: inboundContent.slice(0, 120),
        sender,
        channel,
        conversationLabel: threadLabel,
        timestamp: new Date(),
        priority: 60,
      });
      if (this.currentThread() !== sessionKey) {
        this.panels.suggestInbox();
      }
      this.syncInboxBadge();
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
      this.conversations.addInboxItem({
        sessionKey,
        kind: 'outbound',
        preview: String(event.response).slice(0, 120),
        sender,
        channel,
        conversationLabel: threadLabel,
        timestamp: new Date(),
        priority: 10,
        read: true,
      });
      this.messages.update(msgs => {
        const alreadyShown = msgs.some(m =>
          m.type === 'channel_outbound'
          && (m as any).sessionKey === sessionKey
          && m.content === event.response
        );
        if (alreadyShown) return msgs;
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
    return isChatSystemInjection(text);
  }
}
