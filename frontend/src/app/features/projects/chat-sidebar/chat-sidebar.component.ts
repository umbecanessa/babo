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
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { HttpClient } from '@angular/common/http';
import { Subscription } from 'rxjs';
import { WebSocketService } from '../../../core/services/websocket.service';
import { ApiService, FileAttachment } from '../../../core/services/api.service';
import { PlatformService } from '../../../core/services/platform.service';
import { ConversationService } from '../../../core/services/conversation.service';
import { ChatAttachmentService } from '../../../core/services/chat-attachment.service';
import { VoiceRecorderService } from '../../../core/services/voice-recorder.service';
import { ToastService } from '../../../shared/toast/toast.service';
import { composerDestination } from '../../../core/services/composer-destination.util';
import { isFolderAttachment } from '../../../core/utils/chat-drop.util';
import { parseThinking } from '../../../shared/signal-utils';
import { MarkdownPipe } from '../../../shared/pipes/markdown.pipe';

interface ChatMessage {
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: number;
  source?: string;
}

@Component({
  selector: 'app-chat-sidebar',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, MarkdownPipe],
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
  pendingAttachments = signal<FileAttachment[]>([]);
  fileUploading = signal(false);
  isDragOver = signal(false);

  readonly isFolderAttachment = isFolderAttachment;
  readonly conversations = inject(ConversationService);

  /** Private desk threads only — no Discord/WhatsApp/etc. in Projects sidebar. */
  readonly websocketThreads = computed(() =>
    this.conversations.threads().filter((t) => t.channel === 'websocket'),
  );

  readonly surfaceThreadCount = computed(() =>
    this.conversations.threads().filter((t) => t.channel !== 'websocket').length,
  );

  readonly composerHint = computed(() => {
    const meta = this.websocketThreads().find((t) => t.key === this.activeThread());
    return composerDestination(meta ?? { key: 'websocket:main', label: 'Home', channel: 'websocket' });
  });

  private wsSub?: Subscription;
  private prevAgentId = '';

  constructor(
    private ws: WebSocketService,
    private api: ApiService,
    private http: HttpClient,
    private platform: PlatformService,
    public voice: VoiceRecorderService,
    private chatAttachments: ChatAttachmentService,
    private toast: ToastService,
  ) {}

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
      this.messages.update((msgs) => [
        ...msgs,
        {
          role: 'system' as const,
          content: 'Not connected to agent. Try again in a moment.',
          timestamp: Date.now() / 1000,
        },
      ]);
      return;
    }

    const threadKey = this.activeThread();

    this.messages.update((msgs) => [
      ...msgs,
      { role: 'user' as const, content: msg || '(attachment)', timestamp: Date.now() / 1000 },
    ]);

    this.sending.set(true);
    this.input.set('');
    this.pendingAttachments.set([]);
    this.streamingText.set('');

    if (attachments.length > 0) {
      this.ws.send({
        type: 'message',
        content: msg || 'Please examine the attached files.',
        attachments,
        session_key: threadKey,
      });
    } else {
      this.ws.sendMessage(msg, threadKey);
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
    this.loadingHistory.set(true);
    this.messages.set([]);
    this.streamingText.set('');

    const url = this.platform.isElectron
      ? `${(window as any).nls?.runtimeUrl || 'http://127.0.0.1:9222'}/sessions/${this.agentId}/${encodeURIComponent(sessionKey)}`
      : `${this.api.apiBase}/agents/${this.agentId}/sessions/${encodeURIComponent(sessionKey)}`;

    this.http.get<any>(url).subscribe({
      next: (res) => {
        this.messages.set(this.parseHistoryMessages(res?.messages || []));
        this.loadingHistory.set(false);
      },
      error: () => {
        this.loadingHistory.set(false);
      },
    });
  }

  private parseHistoryMessages(raw: any[]): ChatMessage[] {
    const restored: ChatMessage[] = [];
    for (const m of raw) {
      if (m.role === 'assistant' && m.content) {
        const meta = m.metadata;
        if (meta?.autonomous && meta?.communicated) continue;
        const thought = parseThinking(String(m.content));
        const text = thought.response || String(m.content);
        if (!text.trim()) continue;
        restored.push({
          role: 'assistant',
          content: text,
          timestamp: m.timestamp || 0,
          source: 'history',
        });
      } else if (m.role === 'user' && m.content) {
        const text = String(m.content);
        if (!text.trim()) continue;
        restored.push({
          role: 'user',
          content: text,
          timestamp: m.timestamp || 0,
        });
      }
    }
    return restored;
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

    const sk = msg.session_key || msg.sessionKey || 'websocket:main';

    switch (msg.type) {
      case 'history':
        if (this.activeThread() !== 'websocket:main') return;
        if (this.loadingHistory() || this.messages().length > 0) return;
        if (Array.isArray(msg.messages) && msg.messages.length > 0) {
          this.messages.set(this.parseHistoryMessages(msg.messages));
        }
        this.loadingHistory.set(false);
        break;

      case 'token':
        if (!this.matchesActiveThread(sk, msg)) return;
        this.sending.set(true);
        this.streamingText.update((t) => t + (msg.content || ''));
        break;

      case 'response_replace':
        if (!this.matchesActiveThread(sk, msg)) return;
        this.sending.set(true);
        this.streamingText.set(msg.response || '');
        break;

      case 'response_end': {
        if (!this.matchesActiveThread(sk, msg)) return;
        const fullText = msg.response || this.streamingText() || '';
        const thought = parseThinking(fullText);
        const content = (thought.response || fullText).trim();
        if (content) {
          this.messages.update((msgs) => [
            ...msgs,
            {
              role: 'assistant',
              content,
              timestamp: Date.now() / 1000,
            },
          ]);
        }
        this.streamingText.set('');
        this.sending.set(false);
        break;
      }

      case 'communicate': {
        if (!this.matchesActiveThread(sk, msg)) return;
        if (msg.autonomous && !msg.user_facing) return;
        this.streamingText.set('');
        this.messages.update((msgs) => [
          ...msgs,
          {
            role: 'assistant',
            content: msg.message || '',
            timestamp: Date.now() / 1000,
            source: msg.source || 'communicate',
          },
        ]);
        this.sending.set(false);
        break;
      }

      case 'error':
        if (!this.matchesActiveThread(sk, msg)) return;
        this.messages.update((msgs) => [
          ...msgs,
          {
            role: 'system',
            content: msg.message || msg.content || 'Something went wrong.',
            timestamp: Date.now() / 1000,
          },
        ]);
        this.streamingText.set('');
        this.sending.set(false);
        break;

      default:
        break;
    }
  }
}
