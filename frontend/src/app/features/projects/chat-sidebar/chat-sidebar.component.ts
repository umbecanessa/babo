import {
  Component,
  Input,
  Output,
  EventEmitter,
  OnInit,
  OnDestroy,
  OnChanges,
  SimpleChanges,
  signal,
  computed,
  inject,
  ElementRef,
  ViewChild,
  effect,
  HostListener,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { HttpClient } from '@angular/common/http';
import { Subscription } from 'rxjs';
import { WebSocketService, ChatMessage } from '../../../core/services/websocket.service';
import { ApiService, FileAttachment } from '../../../core/services/api.service';
import { PlatformService } from '../../../core/services/platform.service';
import { ConversationService } from '../../../core/services/conversation.service';
import { ChatAttachmentService } from '../../../core/services/chat-attachment.service';
import { VoiceRecorderService } from '../../../core/services/voice-recorder.service';
import { ToastService } from '../../../shared/toast/toast.service';
import { ConfirmDialogComponent } from '../../../shared/confirm-dialog/confirm-dialog.component';
import { ThreadConfirmRequest } from '../../../shared/thread-dialog/thread-dialog.types';
import { composerDestination } from '../../../core/services/composer-destination.util';
import { isFolderAttachment } from '../../../core/utils/chat-drop.util';
import { parseThinking } from '../../../shared/signal-utils';
import { MessageListComponent } from '../../chat/message-list/message-list.component';
import { ChatModelPickerComponent } from '../../chat/chat-model-picker/chat-model-picker.component';
import { ChatOrchestrationProfilePickerComponent } from '../../chat/chat-orchestration-profile-picker/chat-orchestration-profile-picker.component';
import { AgentModelService } from '../../../core/services/agent-model.service';
import { AgentOrchestrationProfileService } from '../../../core/services/agent-orchestration-profile.service';
import { ChatMainTranscriptService } from '../../../core/services/chat-main-transcript.service';
import { ChatWorkbenchService } from '../../../core/services/chat-workbench.service';
import { restoreChatMessagesFromTranscript, transcriptHasAgenticTrace } from '../../../core/services/chat-transcript-restore.util';
import { agenticAbortLabel } from '../../chat/orchestration-ui.util';

@Component({
  selector: 'app-chat-sidebar',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    RouterLink,
    MessageListComponent,
    ChatModelPickerComponent,
    ChatOrchestrationProfilePickerComponent,
    ConfirmDialogComponent,
  ],
  templateUrl: './chat-sidebar.component.html',
  styleUrl: './chat-sidebar.component.scss',
})
export class ChatSidebarComponent implements OnInit, OnDestroy, OnChanges {
  @Input() agentId = '';
  @Output() close = new EventEmitter<void>();

  @ViewChild('fileInput') fileInput?: ElementRef<HTMLInputElement>;
  @ViewChild('folderInput') folderInput?: ElementRef<HTMLInputElement>;
  @ViewChild('chatInput') chatInput?: ElementRef<HTMLInputElement>;

  messages = signal<ChatMessage[]>([]);
  input = signal('');
  sending = signal(false);
  loadingHistory = signal(false);
  activeThread = signal('websocket:main');
  streamingText = signal('');
  streamingReasoning = signal('');
  awaitingResponse = signal(false);
  agenticActive = signal(false);
  agenticStep = signal(0);
  agenticMaxSteps = signal(15);
  pendingAttachments = signal<FileAttachment[]>([]);
  fileUploading = signal(false);
  isDragOver = signal(false);

  readonly isFolderAttachment = isFolderAttachment;
  readonly conversations = inject(ConversationService);
  private readonly agentModels = inject(AgentModelService);
  private readonly orchProfiles = inject(AgentOrchestrationProfileService);
  private readonly mainTranscript = inject(ChatMainTranscriptService);
  private readonly workbench = inject(ChatWorkbenchService);

  /** Private desk threads only — no Discord/WhatsApp/etc. in Projects sidebar. */
  readonly websocketThreads = computed(() => {
    const home = this.conversations.defaultHomeKey(this.agentId);
    return this.conversations.threads()
      .filter((t) => t.channel === 'websocket')
      .sort((a, b) => {
        if (a.key === home) return -1;
        if (b.key === home) return 1;
        return 0;
      });
  });

  readonly surfaceThreadCount = computed(() =>
    this.conversations.threads().filter((t) => t.channel !== 'websocket').length,
  );

  readonly composerHint = computed(() => {
    const meta = this.websocketThreads().find((t) => t.key === this.activeThread());
    const isHome = this.conversations.isDefaultHome(this.activeThread(), this.agentId);
    return composerDestination(
      meta ?? { key: 'websocket:main', label: 'Private desk', channel: 'websocket' },
      isHome,
    );
  });

  readonly visibleMessages = computed(() =>
    this.conversations.messagesForThread(this.messages(), this.activeThread(), this.agentId),
  );

  readonly showThreadMenu = computed(() => {
    const key = this.activeThread();
    return key === 'websocket:main' || this.conversations.isWebsocketBranch(key);
  });

  branchMenuOpen = signal(false);
  renamingBranch = signal(false);
  renameDraft = signal('');
  threadConfirm = signal<ThreadConfirmRequest | null>(null);

  private wsSub?: Subscription;
  private prevAgentId = '';
  private _agenticStepEvents: Array<{
    step: number;
    toolCalls: { name: string }[];
    toolResults: { success: boolean }[];
    durationMs: number;
  }> = [];

  constructor(
    private ws: WebSocketService,
    private api: ApiService,
    private http: HttpClient,
    private platform: PlatformService,
    public voice: VoiceRecorderService,
    private chatAttachments: ChatAttachmentService,
    private toast: ToastService,
  ) {
    effect(() => {
      this.mainTranscript.revision();
      this.pullFromMainTranscript();
    });
  }

  ngOnInit(): void {
    this.bootstrap();
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['agentId'] && !changes['agentId'].firstChange) {
      this.bootstrap();
    }
  }

  ngOnDestroy(): void {
    this.wsSub?.unsubscribe();
  }

  @HostListener('document:click')
  closeBranchMenu(): void {
    this.branchMenuOpen.set(false);
  }

  toggleBranchMenu(event: MouseEvent): void {
    event.stopPropagation();
    this.branchMenuOpen.update((open) => !open);
  }

  selectThread(key: string): void {
    this.branchMenuOpen.set(false);
    this.activeThread.set(key);
    this.workbench.setActiveSessionKey(key);
    this.conversations.markThreadRead(key);
    this.loadHistory(key);
  }

  renameActiveBranch(): void {
    const key = this.activeThread();
    if (!this.conversations.isWebsocketBranch(key)) return;
    this.branchMenuOpen.set(false);
    const current = this.conversations.threads().find((t) => t.key === key);
    this.renameDraft.set(current?.label || 'Branch');
    this.renamingBranch.set(true);
  }

  updateRenameDraft(value: string): void {
    this.renameDraft.set(value);
  }

  onRenameKeydown(event: KeyboardEvent): void {
    if (event.key === 'Enter') {
      event.preventDefault();
      this.commitRename();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      this.cancelRename();
    }
  }

  onRenameBlur(): void {
    queueMicrotask(() => {
      if (!this.renamingBranch()) return;
      this.commitRename();
    });
  }

  private commitRename(): void {
    const key = this.activeThread();
    const trimmed = this.renameDraft().trim();
    this.renamingBranch.set(false);
    if (!trimmed || !this.conversations.isWebsocketBranch(key)) return;
    this.conversations.renameBranch(key, trimmed);
    if (this.agentId) {
      this.api.renameSession(this.agentId, key, trimmed).subscribe({ error: () => {} });
    }
  }

  cancelRename(): void {
    this.renamingBranch.set(false);
    this.renameDraft.set('');
  }

  deleteActiveBranch(): void {
    const key = this.activeThread();
    if (!this.conversations.isWebsocketBranch(key)) return;
    if (this.conversations.isDefaultHome(key, this.agentId)) {
      this.toast.show(
        'Cannot delete the current Home thread. Reset Home or set another thread as Home first.',
        'error',
      );
      return;
    }
    this.branchMenuOpen.set(false);
    const current = this.conversations.threads().find((t) => t.key === key);
    this.threadConfirm.set({
      title: 'Delete branch',
      message: `Delete branch "${current?.label || key}" and its history?`,
      confirmLabel: 'Delete',
      variant: 'danger',
      action: 'delete_branch',
      sessionKey: key,
    });
  }

  promoteToHome(): void {
    const key = this.activeThread();
    if (!this.agentId || !key || this.conversations.isDefaultHome(key, this.agentId)) return;
    this.branchMenuOpen.set(false);
    const label = this.conversations.displayLabel(
      this.conversations.threads().find((t) => t.key === key)
        || { key, label: key, channel: 'websocket' },
      this.agentId,
    );
    this.threadConfirm.set({
      title: 'Set as Home',
      message:
        `Set "${label}" as Home? Opening this agent will start here. `
        + 'Chat history stays on each thread — nothing is moved.',
      confirmLabel: 'Set as Home',
      variant: 'default',
      action: 'promote_home',
      sessionKey: key,
    });
  }

  resetHome(): void {
    if (!this.agentId) return;
    this.branchMenuOpen.set(false);
    this.threadConfirm.set({
      title: 'Reset Home',
      message:
        'Start a fresh Home thread?\n\n'
        + 'A new branch is created and set as Home. Your current Home stays in the list as a branch. '
        + 'Agent knowledge (facts, memory) is unchanged.',
      confirmLabel: 'Reset Home',
      variant: 'default',
      action: 'reset_home',
      sessionKey: '',
    });
  }

  executeThreadConfirm(): void {
    const req = this.threadConfirm();
    if (!req) return;
    this.threadConfirm.set(null);
    switch (req.action) {
      case 'delete_branch':
        this.performDeleteBranch(req.sessionKey);
        break;
      case 'promote_home':
        this.performPromoteToHome(req.sessionKey);
        break;
      case 'reset_home':
        this.performResetHome();
        break;
    }
  }

  cancelThreadConfirm(): void {
    this.threadConfirm.set(null);
  }

  private performDeleteBranch(key: string): void {
    this.conversations.removeBranch(key);
    this.messages.update((msgs) => msgs.filter((m) => m.sessionKey !== key));
    this.workbench.removeEntriesForSession(key);
    if (this.agentId) {
      this.api.deleteSession(this.agentId, key).subscribe({ error: () => {} });
    }
    this.selectThread(this.conversations.defaultHomeKey(this.agentId));
  }

  private performPromoteToHome(key: string): void {
    if (!this.agentId) return;
    this.api.setDefaultHomeSession(this.agentId, key).subscribe({
      next: () => {
        this.conversations.setDefaultHomeForAgent(this.agentId, key);
        this.selectThread(key);
      },
      error: () => {},
    });
  }

  private performResetHome(): void {
    if (!this.agentId) return;
    const id = Date.now().toString(36);
    const key = `websocket:thread:${id}`;
    const count = this.websocketThreads().filter((t) => this.conversations.isWebsocketBranch(t.key)).length;
    const label = `Branch ${count + 1}`;
    this.conversations.addBranch(label, key);
    this.api.renameSession(this.agentId, key, label).subscribe({ error: () => {} });
    this.api.setDefaultHomeSession(this.agentId, key).subscribe({
      next: () => {
        this.conversations.setDefaultHomeForAgent(this.agentId, key);
        this.selectThread(key);
      },
      error: () => {},
    });
  }

  createNewBranch(): void {
    const id = Date.now().toString(36);
    const key = `websocket:thread:${id}`;
    const count = this.websocketThreads().filter((t) => t.key !== 'websocket:main').length;
    const label = `Branch ${count + 1}`;
    this.conversations.addBranch(label, key);
    this.selectThread(key);
    if (this.agentId) {
      this.api.renameSession(this.agentId, key, label).subscribe({ error: () => {} });
    }
  }

  send(): void {
    const msg = this.input().trim();
    const attachments = this.pendingAttachments();
    if ((!msg && attachments.length === 0) || this.sending()) return;

    if (!this.ws.connected()) {
      this.appendMessages([{
        type: 'status',
        content: 'Not connected to agent. Try again in a moment.',
        timestamp: new Date(),
      }]);
      return;
    }

    const threadKey = this.activeThread();
    const branchLabel = this.conversations.threads().find((t) => t.key === threadKey)?.label;

    this.appendMessages([{
      type: 'user',
      content: msg || '(attachment)',
      timestamp: new Date(),
      sessionKey: this.outgoingSessionKey(threadKey),
    }]);

    this.sending.set(true);
    this.awaitingResponse.set(true);
    this.input.set('');
    this.pendingAttachments.set([]);
    this.streamingText.set('');
    this.streamingReasoning.set('');

    const model = this.agentModels.modelForOutgoingMessage();
    const modelRoute = this.agentModels.modelRouteForOutgoingMessage();
    const orchProfile = this.orchProfiles.profileForOutgoingMessage(this.agentId);

    if (attachments.length > 0) {
      this.ws.send({
        type: 'message',
        content: msg || 'Please examine the attached files.',
        attachments,
        session_key: threadKey,
        ...(branchLabel ? { branch_label: branchLabel } : {}),
        ...(model ? { model } : {}),
        ...(modelRoute ? { model_route: modelRoute } : {}),
        ...(orchProfile ? { orchestration_profile: orchProfile } : {}),
      });
    } else {
      this.ws.sendMessage(msg, threadKey, model, orchProfile, modelRoute, branchLabel);
    }
  }

  onKeydown(event: KeyboardEvent): void {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      this.send();
    }
  }

  updateInput(value: string): void {
    this.input.set(value);
  }

  onAttachClick(event: MouseEvent): void {
    if (event.shiftKey) {
      this.folderInput?.nativeElement.click();
    } else {
      this.fileInput?.nativeElement.click();
    }
  }

  onFilesSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    const files = input.files ? Array.from(input.files) : [];
    input.value = '';
    if (!files.length) return;
    this.uploadFiles(files);
  }

  onDragOver(event: DragEvent): void {
    event.preventDefault();
    event.stopPropagation();
    this.isDragOver.set(true);
  }

  onDragLeave(event: DragEvent): void {
    event.preventDefault();
    event.stopPropagation();
    this.isDragOver.set(false);
  }

  onDrop(event: DragEvent): void {
    event.preventDefault();
    event.stopPropagation();
    this.isDragOver.set(false);
    const dt = event.dataTransfer;
    if (!dt) return;
    this.uploadFromDataTransfer(dt);
  }

  removeAttachment(path: string): void {
    this.pendingAttachments.update((list) => list.filter((a) => a.path !== path));
  }

  async startVoice(): Promise<void> {
    try {
      await this.voice.startRecording();
    } catch (err) {
      console.error('Microphone access denied:', err);
      this.toast.show('Microphone access denied', 'error');
    }
  }

  async stopVoice(): Promise<void> {
    try {
      const blob = await this.voice.stopRecording();
      this.api.transcribe(blob).subscribe({
        next: (result) => {
          this.input.set(result.text);
          this.voice.finishTranscribing();
          setTimeout(() => this.chatInput?.nativeElement?.focus(), 50);
        },
        error: (err) => {
          console.error('Transcription failed:', err);
          this.toast.show('Transcription failed', 'error');
          this.voice.finishTranscribing();
        },
      });
    } catch (err) {
      console.error('Stop recording failed:', err);
      this.voice.cancelRecording();
    }
  }

  cancelVoice(): void {
    this.voice.cancelRecording();
  }

  formatDuration(secs: number): string {
    const m = Math.floor(secs / 60);
    const s = secs % 60;
    return `${m}:${s.toString().padStart(2, '0')}`;
  }

  canSend(): boolean {
    return !!(this.input().trim() || this.pendingAttachments().length) && !this.sending();
  }

  private appendMessages(added: ChatMessage[]): void {
    this.messages.update((msgs) => [...msgs, ...added]);
    this.syncMainTranscript();
  }

  private setMessages(next: ChatMessage[]): void {
    this.messages.set(next);
    this.syncMainTranscript();
  }

  private syncMainTranscript(): void {
    if (!this.agentId) return;
    const home = this.conversations.defaultHomeKey(this.agentId);
    if (this.activeThread() !== home) return;
    this.mainTranscript.replace(
      this.agentId,
      this.conversations.homeMessages(this.messages(), this.agentId),
    );
  }

  private pullFromMainTranscript(): void {
    if (!this.agentId || this.agenticActive()) return;
    const home = this.conversations.defaultHomeKey(this.agentId);
    if (this.activeThread() !== home) return;
    const shared = this.mainTranscript.get(this.agentId);
    const local = this.conversations.homeMessages(this.messages(), this.agentId);
    if (shared.length > local.length) {
      const branch = this.conversations.nonHomeMessages(this.messages(), this.agentId);
      this.messages.set([...branch, ...structuredClone(shared)]);
    }
  }

  private outgoingSessionKey(threadKey: string): string | undefined {
    const home = this.conversations.defaultHomeKey(this.agentId);
    if (threadKey === 'websocket:main' && home === 'websocket:main') {
      return undefined;
    }
    return threadKey;
  }

  private uploadFiles(files: File[]): void {
    if (!this.agentId || files.length === 0) return;
    this.fileUploading.set(true);

    this.chatAttachments.uploadFromFileList(this.agentId, files).subscribe({
      next: (uploaded) => {
        if (uploaded.length) {
          this.pendingAttachments.update((list) => [...list, ...uploaded]);
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

  private uploadFromDataTransfer(dataTransfer: DataTransfer): void {
    if (!this.agentId) return;
    this.fileUploading.set(true);

    this.chatAttachments.uploadFromDataTransfer(this.agentId, dataTransfer).subscribe({
      next: (uploaded) => {
        if (uploaded.length) {
          this.pendingAttachments.update((list) => [...list, ...uploaded]);
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

  private bootstrap(): void {
    if (!this.agentId) return;

    this.wsSub?.unsubscribe();
    this.agentModels.bindAgent(this.agentId);
    this.orchProfiles.setActiveAgent(this.agentId);
    this.workbench.bindAgent(this.agentId);
    void this.agentModels.refreshFromConfig();
    this.ws.connect();
    this.ws.joinAgent(this.agentId);
    this.loadPersistedThreads();
    this.subscribeWs();

    if (this.prevAgentId && this.prevAgentId !== this.agentId) {
      this.messages.set([]);
      this.activeThread.set('websocket:main');
      this.pendingAttachments.set([]);
    }
    this.prevAgentId = this.agentId;

    if (!this.websocketThreads().find((t) => t.key === this.activeThread())) {
      this.activeThread.set(this.conversations.defaultHomeKey(this.agentId));
    }
    this.loadHistory(this.activeThread());
  }

  private loadPersistedThreads(): void {
    if (!this.agentId) return;

    const url = this.platform.isElectron
      ? `${(window as any).nls?.runtimeUrl || 'http://127.0.0.1:9222'}/sessions/${this.agentId}`
      : `${this.api.apiBase}/agents/${this.agentId}/sessions`;

    this.http.get<any>(url).subscribe({
      next: (res) => {
        const defaultHome = (res?.default_home_session_key || 'websocket:main').trim();
        if (defaultHome) {
          this.conversations.setDefaultHomeForAgent(this.agentId, defaultHome);
        }
        const sessions: Record<string, any> = res?.sessions || {};
        const restored: {
          key: string;
          label: string;
          channel: string;
          sender?: string;
          subject?: string;
        }[] = [];

        for (const [key, meta] of Object.entries(sessions)) {
          if (key === 'websocket:main') continue;
          const channel = meta?.channel || key.split(':')[0] || 'websocket';
          if (channel === 'team' || channel === 'delegate') continue;
          const sender = meta?.sender || '';
          const subject = meta?.subject || '';
          const label = meta?.label || this.conversations.labelFromSessionKey(key, channel, { sender, subject });
          restored.push({ key, label, channel, sender, subject });
        }
        this.conversations.resetThreadsForAgent(this.agentId, restored);

        if (!this.websocketThreads().find((t) => t.key === this.activeThread())) {
          this.activeThread.set(defaultHome);
        }
      },
      error: () => {},
    });
  }

  private loadHistory(sessionKey: string): void {
    if (!this.agentId) return;
    if (this.agenticActive()) return;

    const isHome = this.conversations.isDefaultHome(sessionKey, this.agentId);

    if (isHome) {
      const shared = this.mainTranscript.get(this.agentId);
      if (shared.length > 0) {
        const branch = this.conversations.nonHomeMessages(this.messages(), this.agentId);
        this.messages.set([...branch, ...structuredClone(shared)]);
      }
    } else {
      this.messages.update((msgs) => msgs.filter((m) => m.sessionKey !== sessionKey));
    }

    this.loadingHistory.set(true);
    this.streamingText.set('');
    this.streamingReasoning.set('');

    const url = this.platform.isElectron
      ? `${(window as any).nls?.runtimeUrl || 'http://127.0.0.1:9222'}/sessions/${this.agentId}/${encodeURIComponent(sessionKey)}`
      : `${this.api.apiBase}/agents/${this.agentId}/sessions/${encodeURIComponent(sessionKey)}`;

    this.http.get<any>(url).subscribe({
      next: (res) => {
        if (this.agenticActive()) {
          this.loadingHistory.set(false);
          return;
        }
        const sessionMsgs = Array.isArray(res?.messages) ? res.messages : [];
        const restored = restoreChatMessagesFromTranscript(sessionMsgs, { sessionKey });
        if (isHome) {
          const homeMsgs = this.conversations.homeMessages(this.messages(), this.agentId);
          if (restored.length >= homeMsgs.length || sessionMsgs.length > 0) {
            const branch = this.conversations.nonHomeMessages(this.messages(), this.agentId);
            this.setMessages([...branch, ...restored]);
          }
          if (transcriptHasAgenticTrace(sessionMsgs)) {
            this.workbench.hydrateFromTranscript(sessionMsgs, {
              force: !this.workbench.snapshotState().entries.length,
              sessionKey,
            });
          }
          this.syncMainTranscript();
        } else if (restored.length === 0 && sessionMsgs.length === 0) {
          this.messages.update((msgs) => msgs.filter((m) => m.sessionKey !== sessionKey));
          this.workbench.removeEntriesForSession(sessionKey);
        } else {
          this.messages.update((msgs) => {
            const keep = msgs.filter((m) => m.sessionKey !== sessionKey);
            return [...keep, ...restored];
          });
          if (transcriptHasAgenticTrace(sessionMsgs)) {
            this.workbench.hydrateFromTranscript(sessionMsgs, {
              force: true,
              sessionKey,
            });
          }
        }
        this.loadingHistory.set(false);
      },
      error: () => {
        this.loadingHistory.set(false);
      },
    });
  }

  private matchesActiveThread(sessionKey: string, msg: any): boolean {
    const sk = (msg.session_key || msg.sessionKey || sessionKey || this.activeThread() || '').trim();
    return this.conversations.sessionBelongsToThread(
      sk || undefined,
      this.activeThread(),
      this.agentId,
      { messageType: msg.type },
    );
  }

  private subscribeWs(): void {
    this.wsSub = this.ws.onMessage(this.agentId).subscribe((msg: any) => {
      this.handleWsMessage(msg);
    });
  }

  private handleWsMessage(msg: any): void {
    if (!msg?.type) return;

    if (!msg._wbDone) {
      msg._wbDone = true;
      this.workbench.recordFromRuntime(msg);
    }

    const sk = msg.session_key || msg.sessionKey || this.activeThread() || 'websocket:main';

    switch (msg.type) {
      case 'turn_triage':
        this.orchProfiles.noteTriageProfile(this.agentId, {
          profile: msg.profile as string | undefined,
          requested: msg.profile_requested as string | undefined,
          effective: msg.profile_effective as string | undefined,
          floored: msg.profile_floored === true,
        });
        break;

      case 'history': {
        const home = this.conversations.defaultHomeKey(this.agentId);
        if (this.activeThread() !== home) return;
        if (this.agenticActive()) return;
        if (Array.isArray(msg.messages) && msg.messages.length > 0) {
          const restored = restoreChatMessagesFromTranscript(msg.messages, {
            sessionKey: home === 'websocket:main' ? undefined : home,
          });
          const homeMsgs = this.conversations.homeMessages(this.messages(), this.agentId);
          if (restored.length > homeMsgs.length) {
            const branch = this.conversations.nonHomeMessages(this.messages(), this.agentId);
            this.setMessages([...branch, ...restored]);
          }
          if (transcriptHasAgenticTrace(msg.messages)) {
            this.workbench.hydrateFromTranscript(msg.messages, {
              force: !this.workbench.snapshotState().entries.length,
              sessionKey: home,
            });
          }
          this.syncMainTranscript();
        }
        this.loadingHistory.set(false);
        break;
      }

      case 'token':
        if (!this.matchesActiveThread(sk, msg)) return;
        this.sending.set(true);
        this.awaitingResponse.set(true);
        if (msg.thinking) {
          this.streamingReasoning.update((t) => t + (msg.content || ''));
        } else {
          this.streamingText.update((t) => t + (msg.content || ''));
        }
        break;

      case 'response_replace':
        if (!this.matchesActiveThread(sk, msg)) return;
        this.sending.set(true);
        this.awaitingResponse.set(true);
        this.streamingText.set(msg.response || '');
        break;

      case 'response_end': {
        if (!this.matchesActiveThread(sk, msg)) return;
        const fullText = msg.response || this.streamingText() || '';
        const thought = parseThinking(fullText);
        const content = (thought.response || fullText).trim();
        if (content) {
          this.appendMessages([{
            type: 'assistant',
            content,
            reasoning: thought.thinking || undefined,
            timestamp: new Date(),
            sessionKey: this.conversations.resolveDeskSessionKey(sk, this.activeThread(), this.agentId),
          }]);
        }
        this.streamingText.set('');
        this.streamingReasoning.set('');
        this.sending.set(false);
        this.awaitingResponse.set(false);
        break;
      }

      case 'communicate': {
        if (!this.matchesActiveThread(sk, msg)) return;
        if (msg.autonomous && !msg.user_facing) return;
        this.streamingText.set('');
        this.appendMessages([{
          type: 'assistant',
          content: msg.message || '',
          timestamp: new Date(),
          sessionKey: this.conversations.resolveDeskSessionKey(sk, this.activeThread(), this.agentId),
        }]);
        this.sending.set(false);
        this.awaitingResponse.set(false);
        break;
      }

      case 'agentic_start': {
        if (msg.sub_agent === true || msg.autonomous) break;
        if (msg.orchestration_profile) {
          this.orchProfiles.noteTriageProfile(this.agentId, {
            profile: msg.orchestration_profile as string,
          });
        }
        this.agenticActive.set(true);
        this.agenticStep.set(0);
        this.agenticMaxSteps.set(msg.max_steps || 15);
        this._agenticStepEvents = [];
        this.appendMessages([{
          type: 'agentic_start' as any,
          content: `Agent starting task (up to ${msg.max_steps || 15} steps)`,
          timestamp: new Date(),
          sessionKey: this.conversations.resolveDeskSessionKey(sk, this.activeThread(), this.agentId),
        }]);
        break;
      }

      case 'agentic_iteration': {
        if (msg.sub_agent === true || msg.autonomous) break;
        const step = msg.step || 0;
        this.agenticStep.set(step);
        const toolNames = (msg.tool_calls || [])
          .map((tc: any) => tc.name || tc.tool_name || 'tool')
          .join(', ');
        const results = msg.tool_results || [];
        const successes = results.filter((r: any) => r.success !== false).length;
        this._agenticStepEvents.push({
          step,
          toolCalls: (msg.tool_calls || []).map((tc: any) => ({ name: tc.name || 'tool' })),
          toolResults: results.map((tr: any) => ({ success: tr.success !== false })),
          durationMs: msg.duration_ms || 0,
        });
        this.appendMessages([{
          type: 'agentic_iteration' as any,
          content: `Step ${step}: ${toolNames || 'processing'} (${successes}/${results.length} succeeded)`,
          timestamp: new Date(),
          sessionKey: this.conversations.resolveDeskSessionKey(sk, this.activeThread(), this.agentId),
          agentic: {
            step,
            maxSteps: msg.max_steps || this.agenticMaxSteps(),
            toolCalls: msg.tool_calls || [],
            toolResults: results,
            hormones: msg.hormones || {},
            durationMs: msg.duration_ms || 0,
          },
        }]);
        break;
      }

      case 'loop_interrupted': {
        this.awaitingResponse.set(false);
        this.agenticActive.set(false);
        this.agenticStep.set(0);
        const interruptText =
          msg.content
          || `Previous task was interrupted at step ${msg.iteration ?? '?'}. Use Continue to resume.`;
        const resumeToken = String(msg.resume_token || msg.interrupted_at || '');
        const agentKey = this.agentId || sk;
        const storageKey = `loop_interrupt_seen_${agentKey}_${resumeToken}`;
        if (resumeToken && sessionStorage.getItem(storageKey)) {
          break;
        }
        if (resumeToken) {
          sessionStorage.setItem(storageKey, '1');
        }
        this.appendMessages([{
          type: 'status',
          content: interruptText,
          timestamp: new Date(),
          sessionKey: this.conversations.resolveDeskSessionKey(sk, this.activeThread(), this.agentId),
        }]);
        break;
      }

      case 'loop_interrupt_dismissed': {
        break;
      }

      case 'agentic_complete': {
        if (msg.sub_agent === true || msg.autonomous) break;
        const remainingText = this.streamingText();
        if (remainingText) {
          const thought = parseThinking(remainingText);
          this.appendMessages([{
            type: 'assistant',
            content: thought.response || remainingText,
            reasoning: thought.thinking || undefined,
            timestamp: new Date(),
            sessionKey: this.conversations.resolveDeskSessionKey(sk, this.activeThread(), this.agentId),
          }]);
        }
        this.agenticActive.set(false);
        this.agenticStep.set(0);
        this.streamingText.set('');
        this.streamingReasoning.set('');
        this.sending.set(false);
        this.awaitingResponse.set(false);

        const aborted = msg.aborted || false;
        const totalSteps = msg.total_steps || 0;
        const totalToolCalls = msg.total_tool_calls || 0;
        const durationMs = msg.duration_ms || 0;
        const stepEvents = [...this._agenticStepEvents];
        this._agenticStepEvents = [];

        this.appendMessages([{
          type: 'agentic_complete' as any,
          content: agenticAbortLabel(aborted, msg.abort_reason || msg.exit_reason, false),
          timestamp: new Date(),
          sessionKey: this.conversations.resolveDeskSessionKey(sk, this.activeThread(), this.agentId),
          agenticComplete: {
            totalSteps,
            totalToolCalls,
            aborted,
            abortReason: msg.abort_reason || msg.exit_reason || '',
            durationMs,
            hormones: msg.hormones || {},
            events: stepEvents,
          },
        }]);

        if (msg.final_response && !remainingText) {
          const thought = parseThinking(msg.final_response);
          this.appendMessages([{
            type: 'assistant',
            content: thought.response || msg.final_response,
            reasoning: thought.thinking || undefined,
            timestamp: new Date(),
            sessionKey: this.conversations.resolveDeskSessionKey(sk, this.activeThread(), this.agentId),
          }]);
        }
        break;
      }

      case 'error':
        if (!this.matchesActiveThread(sk, msg)) return;
        this.appendMessages([{
          type: 'status',
          content: msg.message || msg.content || 'Something went wrong.',
          timestamp: new Date(),
        }]);
        this.streamingText.set('');
        this.streamingReasoning.set('');
        this.sending.set(false);
        this.awaitingResponse.set(false);
        this.agenticActive.set(false);
        break;

      default:
        break;
    }
  }
}
