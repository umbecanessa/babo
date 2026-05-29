import { Component, Input, Output, EventEmitter, OnInit, OnDestroy, OnChanges, SimpleChanges, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Subscription } from 'rxjs';
import { WebSocketService } from '../../../core/services/websocket.service';
import { ApiService } from '../../../core/services/api.service';
import { MarkdownPipe } from '../../../shared/pipes/markdown.pipe';

interface ChatMessage {
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: number;
  source?: string;
}

interface ThreadOption {
  id: string;
  label: string;
  sessionKey: string;
}

@Component({
  selector: 'app-chat-sidebar',
  standalone: true,
  imports: [CommonModule, FormsModule, MarkdownPipe],
  templateUrl: './chat-sidebar.component.html',
  styleUrl: './chat-sidebar.component.scss',
})
export class ChatSidebarComponent implements OnInit, OnDestroy, OnChanges {
  @Input() agentId = '';
  @Output() close = new EventEmitter<void>();

  messages = signal<ChatMessage[]>([]);
  input = signal('');
  sending = signal(false);
  loadingHistory = signal(false);
  activeThread = signal('websocket:main');
  threads = signal<ThreadOption[]>([]);

  private wsSub?: Subscription;

  constructor(
    private ws: WebSocketService,
    private api: ApiService,
  ) {}

  ngOnInit(): void {
    this.subscribeWs();
    this.loadSessions();
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['agentId'] && !changes['agentId'].firstChange) {
      this.loadSessions();
    }
  }

  ngOnDestroy(): void {
    this.wsSub?.unsubscribe();
  }

  selectThread(id: string): void {
    this.activeThread.set(id);
    const thread = this.threads().find(t => t.id === id);
    if (thread) {
      this.loadHistory(thread.sessionKey);
    }
  }

  send(): void {
    const msg = this.input().trim();
    if (!msg || this.sending()) return;

    this.messages.update(msgs => [...msgs, {
      role: 'user' as const,
      content: msg,
      timestamp: Date.now() / 1000,
    }]);

    this.sending.set(true);
    this.input.set('');

    this.api.sendCommand(this.agentId, msg, {
      view: 'projects',
      thread: this.activeThread(),
    }).subscribe({
      next: (res) => {
        if (res.response) {
          this.messages.update(msgs => [...msgs, {
            role: 'assistant' as const,
            content: res.response,
            timestamp: Date.now() / 1000,
            source: 'command',
          }]);
        }
        this.sending.set(false);
      },
      error: () => {
        this.messages.update(msgs => [...msgs, {
          role: 'system' as const,
          content: 'Failed to send message.',
          timestamp: Date.now() / 1000,
        }]);
        this.sending.set(false);
      },
    });
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

  private loadSessions(): void {
    if (!this.agentId) return;

    this.api.listSessions(this.agentId).subscribe({
      next: (res) => {
        const sessions = res.sessions || {};
        const threadList: ThreadOption[] = [];

        for (const [key, meta] of Object.entries<any>(sessions)) {
          const label = this.sessionKeyToLabel(key, meta);
          threadList.push({ id: key, label, sessionKey: key });
        }

        if (threadList.length === 0) {
          threadList.push({
            id: 'websocket:main',
            label: 'Main Chat',
            sessionKey: 'websocket:main',
          });
        }

        this.threads.set(threadList);

        if (!threadList.find(t => t.id === this.activeThread())) {
          this.activeThread.set(threadList[0]?.id || 'websocket:main');
        }

        this.loadHistory(this.activeThread());
      },
      error: () => {
        this.threads.set([{
          id: 'websocket:main',
          label: 'Main Chat',
          sessionKey: 'websocket:main',
        }]);
        this.loadHistory('websocket:main');
      },
    });
  }

  private loadHistory(sessionKey: string): void {
    if (!this.agentId) return;
    this.loadingHistory.set(true);
    this.messages.set([]);

    this.api.getSessionHistory(this.agentId, sessionKey).subscribe({
      next: (res) => {
        const msgs = (res.messages || []) as any[];
        this.messages.set(msgs.map((m: any) => ({
          role: m.role as 'user' | 'assistant',
          content: typeof m.content === 'string' ? m.content : JSON.stringify(m.content),
          timestamp: m.timestamp || 0,
          source: m.role === 'assistant' ? 'history' : undefined,
        })));
        this.loadingHistory.set(false);
      },
      error: () => {
        this.loadingHistory.set(false);
      },
    });
  }

  private sessionKeyToLabel(key: string, meta?: any): string {
    if (meta?.label) return meta.label;
    if (key === 'websocket:main') return 'Main Chat';
    if (key.includes('whatsapp')) return 'WhatsApp';
    if (key.includes('telegram')) return 'Telegram';
    if (key.includes('email')) return 'Email';
    if (key.includes('remote')) return 'Remote';
    const parts = key.split(':');
    return parts[parts.length - 1] || key;
  }

  private subscribeWs(): void {
    this.wsSub = this.ws.onMessage().subscribe((msg: any) => {
      if (msg?.type === 'communicate' && msg.message) {
        if (msg.autonomous && !msg.user_facing) {
          return;
        }
        this.messages.update(msgs => [...msgs, {
          role: 'assistant' as const,
          content: msg.message,
          timestamp: Date.now() / 1000,
          source: msg.source || 'autonomous',
        }]);
      }
    });
  }
}
