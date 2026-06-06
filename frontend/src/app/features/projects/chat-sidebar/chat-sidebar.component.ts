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
import { restoreChatMessagesFromTranscript } from '../../../core/services/chat-transcript-restore.util';
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
  readonly websocketThreads = computed(() =>
    this.conversations.threads().filter((t) => t.channel === 'websocket'),
  );

  readonly surfaceThreadCount = computed(() =>
    this.conversations.threads().filter((t) => t.channel !== 'websocket').length,
  );

  readonly composerHint = computed(() => {
    const meta = this.websocketThreads().find((t) => t.key === this.activeThread());
    return composerDestination(meta ?? { key: 'websocket:main', label: 'Private desk', channel: 'websocket' });
  });

  readonly visibleMessages = computed(() => {
    const thread = this.activeThread();
    return this.messages().filter((m) => {
      const sk = m.sessionKey || 'websocket:main';
      return thread === 'websocket:main'
        ? sk === 'websocket:main'
        : sk === thread;
    });
  });

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

  selectThread(key: string): void {
    this.activeThread.set(key);
    this.conversations.markThreadRead(key);
    this.loadHistory(key);
  }

  createNewBranch(): void {
    const id = Date.now().toString(36);
    const key = `websocket:thread:${id}`;
    const count = this.websocketThreads().filter((t) => t.key !== 'websocket:main').length;
    this.conversations.addBranch(`Branch ${count + 1}`, key);
    this.selectThread(key);
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

    this.appendMessages([{
      type: 'user',
      content: msg || '(attachment)',
      timestamp: new Date(),
      sessionKey: threadKey !== 'websocket:main' ? threadKey : undefined,
    }]);

    this.sending.set(true);
    this.awaitingResponse.set(true);
    this.input.set('');
    this.pendingAttachments.set([]);
    this.streamingText.set('');
    this.streamingReasoning.set('');

    const model = this.agentModels.modelForOutgoingMessage();
    const orchProfile = this.orchProfiles.profileForOutgoingMessage(this.agentId);

    if (attachments.length > 0) {
      this.ws.send({
        type: 'message',
        content: msg || 'Please examine the attached files.',
        attachments,
        session_key: threadKey,
        ...(model ? { model } : {}),
        ...(orchProfile ? { orchestration_profile: orchProfile } : {}),
      });
    } else {
      this.ws.sendMessage(msg, threadKey, model, orchProfile);
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
    if (!this.agentId || this.activeThread() !== 'websocket:main') return;
    const mainMsgs = this.messages().filter(
      (m) => !m.sessionKey || m.sessionKey === 'websocket:main',
    );
    this.mainTranscript.replace(this.agentId, mainMsgs);
  }

  private pullFromMainTranscript(): void {
    if (!this.agentId || this.activeThread() !== 'websocket:main' || this.agenticActive()) return;
    const shared = this.mainTranscript.get(this.agentId);
    const local = this.messages().filter(
      (m) => !m.sessionKey || m.sessionKey === 'websocket:main',
    );
    if (shared.length > local.length) {
      const branch = this.messages().filter(
        (m) => m.sessionKey && m.sessionKey !== 'websocket:main',
      );
      this.messages.set([...branch, ...structuredClone(shared)]);
    }
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
      this.activeThread.set('websocket:main');
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
          this.activeThread.set('websocket:main');
        }
      },
      error: () => {},
    });
  }

  private loadHistory(sessionKey: string): void {
    if (!this.agentId) return;

    if (sessionKey === 'websocket:main') {
      const shared = this.mainTranscript.get(this.agentId);
      if (shared.length > 0) {
        this.setMessages(structuredClone(shared));
        this.loadingHistory.set(false);
        return;
      }
    }

    this.loadingHistory.set(true);
    this.messages.update((msgs) =>
      msgs.filter((m) => (m.sessionKey || 'websocket:main') !== sessionKey),
    );
    this.streamingText.set('');
    this.streamingReasoning.set('');

    const url = this.platform.isElectron
      ? `${(window as any).nls?.runtimeUrl || 'http://127.0.0.1:9222'}/sessions/${this.agentId}/${encodeURIComponent(sessionKey)}`
      : `${this.api.apiBase}/agents/${this.agentId}/sessions/${encodeURIComponent(sessionKey)}`;

    this.http.get<any>(url).subscribe({
      next: (res) => {
        const restored = restoreChatMessagesFromTranscript(res?.messages || [], { sessionKey });
        this.messages.update((msgs) => {
          const keep = msgs.filter((m) => (m.sessionKey || 'websocket:main') !== sessionKey);
          return [...keep, ...restored];
        });
        if (sessionKey === 'websocket:main') {
          this.syncMainTranscript();
        }
        this.loadingHistory.set(false);
      },
      error: () => {
        this.loadingHistory.set(false);
      },
    });
  }

  private matchesActiveThread(sessionKey: string, msg: any): boolean {
    const thread = this.activeThread();
    const sk = sessionKey || 'websocket:main';
    if (thread === 'websocket:main') {
      return sk === 'websocket:main' || (!msg.session_key && !msg.sessionKey);
    }
    return sk === thread;
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

    const sk = msg.session_key || msg.sessionKey || 'websocket:main';

    switch (msg.type) {
      case 'turn_triage':
        this.orchProfiles.noteTriageProfile(this.agentId, {
          profile: msg.profile as string | undefined,
          requested: msg.profile_requested as string | undefined,
          effective: msg.profile_effective as string | undefined,
          floored: msg.profile_floored === true,
        });
        break;

      case 'history':
        if (this.activeThread() !== 'websocket:main') return;
        if (this.agenticActive()) return;
        if (Array.isArray(msg.messages) && msg.messages.length > 0) {
          const shared = this.mainTranscript.get(this.agentId);
          if (shared.length >= msg.messages.length) {
            this.pullFromMainTranscript();
          } else {
            const restored = restoreChatMessagesFromTranscript(msg.messages);
            this.setMessages(restored);
          }
        }
        this.loadingHistory.set(false);
        break;

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
            sessionKey: sk !== 'websocket:main' ? sk : undefined,
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
          sessionKey: sk !== 'websocket:main' ? sk : undefined,
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
          sessionKey: sk !== 'websocket:main' ? sk : undefined,
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
          sessionKey: sk !== 'websocket:main' ? sk : undefined,
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
            sessionKey: sk !== 'websocket:main' ? sk : undefined,
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
          sessionKey: sk !== 'websocket:main' ? sk : undefined,
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
            sessionKey: sk !== 'websocket:main' ? sk : undefined,
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
