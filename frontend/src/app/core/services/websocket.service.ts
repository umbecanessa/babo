import { Injectable, signal } from '@angular/core';
import { io, Socket } from 'socket.io-client';
import { Subject, Observable } from 'rxjs';
import { environment } from '../../../environments/environment';
import { AuthService } from './auth.service';
import { PlatformService } from './platform.service';

export interface DriveActionData {
  name: string;
  actionType: string;
  domain: string;
  query: string;
  success: boolean;
  resultPreview: string;
  willToAct: number;
}

export interface DrowsyData {
  reason: string;
  actions: string[];
}

// ---------------------------------------------------------------------------
// Agentic loop event data
// ---------------------------------------------------------------------------

export interface AgenticToolCall {
  name: string;
  arguments: Record<string, any>;
  source: string;
}

export interface AgenticToolResult {
  tool: string;
  success: boolean;
  result_preview: string;
}

export interface AgenticIterationData {
  step: number;
  maxSteps: number;
  toolCalls: AgenticToolCall[];
  toolResults: AgenticToolResult[];
  hormones: Record<string, number>;
  durationMs: number;
}

export interface AgenticStepEvent {
  step: number;
  toolCalls: { name: string }[];
  toolResults: { success: boolean }[];
  durationMs: number;
}

export interface AgenticCompleteData {
  totalSteps: number;
  totalToolCalls: number;
  aborted: boolean;
  abortReason: string;
  durationMs: number;
  hormones: Record<string, number>;
  events?: AgenticStepEvent[];
}

// ---------------------------------------------------------------------------
// Chat message
// ---------------------------------------------------------------------------

export interface MessageAttachment {
  name: string;
  path: string;
  mime_type: string;
  size?: number;
}

export interface ChatMessage {
  type:
    | 'user'
    | 'assistant'
    | 'status'
    | 'token'
    | 'tool_use'
    | 'drive_action'
    | 'reach_out'
    | 'drowsy'
    | 'agentic_start'
    | 'agentic_iteration'
    | 'agentic_complete'
    | 'activity_status'
    | 'tool_output_chunk'
    | 'turn_thinking'
    | 'browser_navigation'
    | 'copilot_ack'
    | 'agentic_token'
    | 'tool_execution_start'
    | 'tool_execution_end'
    | 'tool_progress'
    | 'tool_call_delta'
    | 'agentic_plan'
    | 'browser_preview'
    | 'browser_command'
    | 'ask_user'
    | 'communicate'
    | 'user_answer'
    | 'safety_net_learned'
    | 'delegate_start'
    | 'delegate_end'
    | 'delegate_card'
    | 'channel_inbound'
    | 'channel_outbound'
    | 'connection_request'
    | 'probe_signal'
    | 'team_created'
    | 'team_launched'
    | 'team_advanced'
    | 'team_paused'
    | 'team_resumed'
    | 'team_disbanded'
    | 'team_member_complete'
    | 'team_member_progress'
    | 'file_changed';
  content: string;
  /** File attachments sent by the user with this message. */
  attachments?: MessageAttachment[];
  /** Channel message metadata */
  channel?: string;
  sender?: string;
  subject?: string;
  sessionKey?: string;
  /** Agent's internal reasoning / chain-of-thought (from <think> blocks). */
  reasoning?: string;
  timestamp?: Date;
  nls?: any;
  tool?: { name: string; query: string; source: string; preview: string; success: boolean };
  drive?: DriveActionData;
  drowsy?: DrowsyData;
  agentic?: AgenticIterationData;
  agenticComplete?: AgenticCompleteData;
  activityText?: string;
  /** Live bash output chunk */
  toolOutputChunk?: string;
  toolOutputName?: string;
  /** Agent's thinking for current iteration */
  thinking?: string;
  thinkingIteration?: number;
  /** Streaming agentic token */
  agenticToken?: string;
  /** Tool execution progress */
  toolProgress?: {
    toolName: string;
    callId: string;
    arguments?: any;
    isError?: boolean;
    resultPreview?: string;
    done?: boolean;
    iteration?: number;
    output?: string;
    details?: Record<string, any>;
    groupCount?: number;
    groupItems?: { label: string; isError: boolean }[];
    groupExpanded?: boolean;
  };
  /** Agentic plan checklist */
  planSteps?: { label: string; status: 'pending' | 'active' | 'done' | 'error'; detail?: string }[];
  /** Browser navigation event */
  browserUrl?: string;
  browserTitle?: string;
  browserAction?: string;
  browserRequestId?: string;
  browserError?: string;
  /** Heartbeat status + elapsed */
  statusType?: string;
  elapsedMs?: number;
  /** Sub-agent delegation card */
  delegate?: {
    number: number;
    task: string;
    status: 'running' | 'done' | 'error';
    toolCalls: { name: string; args?: any; callId?: string; result?: string; isError?: boolean }[];
    summary?: string;
    iterations?: number;
    totalToolCalls?: number;
    maxIterations?: number;
    elapsedSeconds?: number;
    expanded?: boolean;
  };
  /** Generic metadata bag for channel events, reach-out, etc. */
  metadata?: Record<string, any>;
}

@Injectable({ providedIn: 'root' })
export class WebSocketService {
  /** Socket.IO socket (used in browser/NestJS mode) */
  private socket: Socket | null = null;

  /** Raw WebSocket (used in Electron/local runtime mode) */
  private rawWs: WebSocket | null = null;

  /** URL of the active raw WS (idempotent joinAgent across routes). */
  private rawWsTargetUrl: string | null = null;

  private messagesSubject = new Subject<any>();
  private probeSignalSubject = new Subject<{
    signals: Record<string, number>;
    fired: string[];
    iteration: number;
    midGeneration: boolean;
    ts: Date;
  }>();
  private useRawWs = false;
  private runtimeWsUrl = '';

  connected = signal(false);
  currentAgentId = signal<string | null>(null);

  /** Set to true when we know a restart is coming (skill review approved). */
  restartExpected = signal(false);

  /** Current reconnection state: null | 'restarting' | 'reconnecting' */
  reconnectState = signal<string | null>(null);

  /**
   * When > 0, at least one `onMessage(agentId)` stream is active (chat UI).
   * Inbound messages are not buffered in that case (live path only).
   */
  private replaySubscriberCount = 0;

  /** Per-agent ring buffer for WS payloads when chat UI is not subscribed. */
  private messageBuffers = new Map<string, any[]>();

  private static readonly MAX_BUFFERED_PER_AGENT = 400;

  constructor(
    private auth: AuthService,
    private platform: PlatformService,
  ) {
    this.useRawWs = this.platform.isElectron;

    if (this.useRawWs) {
      this.initElectronWsUrl();
    }
  }

  private async initElectronWsUrl(): Promise<void> {
    try {
      const nls = (window as any).nls;
      if (nls?.getUrls) {
        const urls = await nls.getUrls();
        this.runtimeWsUrl = urls.wsUrl;
      }
    } catch {
      this.runtimeWsUrl = (environment as any).wsUrl || 'ws://127.0.0.1:9222';
    }
  }

  connect(): void {
    if (this.useRawWs) {
      // Raw WebSocket mode is per-agent, handled in joinAgent()
      this.connected.set(true);
      return;
    }

    // Socket.IO mode (browser / NestJS proxy)
    if (this.socket?.connected) return;

    const token = this.auth.getAccessToken();
    if (!token) return;

    this.socket = io(`${environment.wsUrl}/chat`, {
      auth: { token },
      transports: ['websocket'],
    });

    this.socket.on('connect', () => {
      this.connected.set(true);
      const agentId = this.currentAgentId();
      if (agentId) {
        this.socket?.emit('join', { agentId });
      }
    });

    this.socket.on('disconnect', () => {
      this.connected.set(false);
    });

    this.socket.on('runtime', (data: any) => this.emitChatMessage(data));

    this.socket.on('joined', (data: any) => {
      this.emitChatMessage({ type: 'status', content: 'Connected to agent', ...data });
    });

    this.socket.on('error', (data: any) => {
      this.emitChatMessage({ type: 'error', content: data.message });
    });

    this.socket.on('runtime_disconnected', () => {
      this.emitChatMessage({ type: 'status', content: 'Runtime disconnected' });
    });
  }

  joinAgent(agentId: string): void {
    // Cancel any in-flight reconnect for the previous agent
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.reconnectState.set(null);

    const prev = this.currentAgentId();
    if (prev != null && prev !== agentId) {
      this.messageBuffers.delete(prev);
    }

    this.currentAgentId.set(agentId);

    if (this.useRawWs) {
      this.connectRawWs(agentId);
      return;
    }

    this.socket?.emit('join', { agentId });
  }

  sendMessage(content: string, sessionKey?: string): void {
    const payload: any = { type: 'message', content };
    if (sessionKey && sessionKey !== 'websocket:main') {
      payload.session_key = sessionKey;
    }

    if (this.useRawWs && this.rawWs?.readyState === WebSocket.OPEN) {
      this.rawWs.send(JSON.stringify(payload));
      return;
    }

    this.socket?.emit('message', payload);
  }

  sendCommand(command: string, args?: Record<string, any>): void {
    if (this.useRawWs && this.rawWs?.readyState === WebSocket.OPEN) {
      this.rawWs.send(JSON.stringify({ type: 'command', command, ...args }));
      return;
    }

    this.socket?.emit('message', { type: 'command', command, ...args });
  }

  send(data: Record<string, any>): void {
    if (this.useRawWs && this.rawWs?.readyState === WebSocket.OPEN) {
      this.rawWs.send(JSON.stringify(data));
      return;
    }

    this.socket?.emit('message', data);
  }

  sendAbort(): void {
    this.sendCommand('abort');
  }

  /**
   * Raw stream (no replay). Use for Tasks, probes, or secondary listeners.
   */
  onMessage(): Observable<any>;
  /**
   * Chat UI: replay per-agent buffer (missed while away from chat), then live.
   */
  onMessage(agentId: string): Observable<any>;
  onMessage(agentId?: string): Observable<any> {
    if (agentId == null || agentId === '') {
      return this.messagesSubject.asObservable();
    }
    return new Observable<any>((observer) => {
      this.replaySubscriberCount++;
      const buf = this.messageBuffers.get(agentId);
      if (buf?.length) {
        for (const m of buf) {
          observer.next(m);
        }
        this.messageBuffers.set(agentId, []);
      }
      const sub = this.messagesSubject.subscribe({
        next: (m) => observer.next(m),
        error: (e) => observer.error(e),
        complete: () => observer.complete(),
      });
      return () => {
        sub.unsubscribe();
        this.replaySubscriberCount--;
      };
    });
  }

  /** Push to subscribers; buffer per agent when no chat replay stream is active. */
  private emitChatMessage(data: any): void {
    const aid = this.currentAgentId();
    if (aid && this.replaySubscriberCount === 0) {
      const arr = this.messageBuffers.get(aid) ?? [];
      arr.push(data);
      if (arr.length > WebSocketService.MAX_BUFFERED_PER_AGENT) {
        arr.splice(0, arr.length - WebSocketService.MAX_BUFFERED_PER_AGENT);
      }
      this.messageBuffers.set(aid, arr);
    }
    this.messagesSubject.next(data);
  }

  onProbeSignal(): Observable<{
    signals: Record<string, number>;
    fired: string[];
    iteration: number;
    midGeneration: boolean;
    ts: Date;
  }> {
    return this.probeSignalSubject.asObservable();
  }

  emitProbeSignal(data: {
    signals: Record<string, number>;
    fired: string[];
    iteration: number;
    midGeneration: boolean;
    ts: Date;
  }): void {
    this.probeSignalSubject.next(data);
  }

  disconnect(): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.reconnectState.set(null);

    if (this.rawWs) {
      this.rawWs.onclose = null;
      this.rawWs.onerror = null;
      this.rawWs.close();
      this.rawWs = null;
    }
    this.rawWsTargetUrl = null;

    if (this.socket) {
      this.socket.disconnect();
      this.socket = null;
    }

    this.connected.set(false);
    this.currentAgentId.set(null);
    this.messageBuffers.clear();
    this.replaySubscriberCount = 0;
  }

  // ─── Raw WebSocket (Electron / local runtime) ─────────────────

  private connectRawWs(agentId: string): void {
    if (agentId !== this.currentAgentId()) return;

    const wsBase = this.runtimeWsUrl || 'ws://127.0.0.1:9222';
    const url = `${wsBase}/ws/chat/${agentId}`;

    // Keep one socket per agent across Chat / Tasks / IDE routes (same agentId).
    if (
      this.rawWs &&
      this.rawWsTargetUrl === url &&
      (this.rawWs.readyState === WebSocket.OPEN ||
        this.rawWs.readyState === WebSocket.CONNECTING)
    ) {
      return;
    }

    if (this.rawWs) {
      this.rawWs.onclose = null;
      this.rawWs.onerror = null;
      this.rawWs.close();
      this.rawWs = null;
    }
    this.rawWsTargetUrl = null;

    this.rawWs = new WebSocket(url);
    this.rawWsTargetUrl = url;

    this.rawWs.onopen = () => {
      this.connected.set(true);
      this.emitChatMessage({
        type: 'status',
        content: 'Connected to agent',
        agent_id: agentId,
      });
    };

    this.rawWs.onmessage = (event: MessageEvent) => {
      try {
        const data = JSON.parse(event.data);
        this.emitChatMessage(data);
      } catch {
        // Non-JSON message, ignore
      }
    };

    this.rawWs.onclose = (ev: CloseEvent) => {
      this.connected.set(false);
      if (ev.code === 4004) {
        this.emitChatMessage({
          type: 'status',
          content: 'Agent no longer exists. Please select or create an agent.',
        });
        this.currentAgentId.set(null);
        this.rawWs = null;
        this.rawWsTargetUrl = null;
        return;
      }
      this.attemptReconnect(agentId);
    };

    this.rawWs.onerror = () => {
      // Only show error if we're not already in a reconnect cycle
      if (!this.reconnectState()) {
        this.emitChatMessage({
          type: 'status',
          content: 'WebSocket connection error',
        });
      }
    };
  }

  // ─── Auto-reconnect ─────────────────────────────────────────

  private reconnectTimer: any = null;
  private reconnectAttempts = 0;
  private readonly MAX_RECONNECT_ATTEMPTS = 30;

  /** Signal that a restart is expected (call before approving a skill review). */
  expectRestart(): void {
    this.restartExpected.set(true);
  }

  private attemptReconnect(agentId: string): void {
    if (agentId !== this.currentAgentId()) return;
    if (this.reconnectTimer) return;

    const state = this.restartExpected() ? 'restarting' : 'reconnecting';
    this.reconnectState.set(state);
    this.reconnectAttempts = 0;

    this.emitChatMessage({
      type: 'status',
      content: state === 'restarting'
        ? 'Server restarting...'
        : 'Connection lost, reconnecting...',
    });

    this.pollAndReconnect(agentId);
  }

  private pollAndReconnect(agentId: string): void {
    if (this.reconnectAttempts >= this.MAX_RECONNECT_ATTEMPTS) {
      this.reconnectState.set(null);
      this.restartExpected.set(false);
      this.emitChatMessage({
        type: 'status',
        content: 'Could not reconnect to server.',
      });
      return;
    }

    const delay = Math.min(2000 * Math.pow(1.3, this.reconnectAttempts), 10000);
    this.reconnectAttempts++;

    this.reconnectTimer = setTimeout(async () => {
      this.reconnectTimer = null;

      if (agentId !== this.currentAgentId()) return;

      try {
        const wsBase = this.runtimeWsUrl || 'ws://127.0.0.1:9222';
        const httpBase = wsBase.replace('ws://', 'http://').replace('wss://', 'https://');
        const resp = await fetch(`${httpBase}/health`, { signal: AbortSignal.timeout(5000) });

        if (resp.ok) {
          const wasRestart = this.restartExpected();
          this.reconnectState.set(null);
          this.restartExpected.set(false);
          this.connectRawWs(agentId);
          this.emitChatMessage({
            type: 'status',
            content: wasRestart
              ? 'Server restarted — skills loaded. You can continue chatting.'
              : 'Reconnected to server.',
          });
          return;
        }
      } catch {
        // Server not ready yet
      }

      this.pollAndReconnect(agentId);
    }, delay);
  }
}
