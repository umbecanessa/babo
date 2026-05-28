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
    teamId?: string;
    waveAttempt?: number;
    teamName?: string;
  };
  /** Generic metadata bag for channel events, reach-out, etc. */
  metadata?: Record<string, any>;
  /** Source agent when multiple agents are connected in parallel. */
  agent_id?: string;
}

interface RawWsEntry {
  ws: WebSocket;
  url: string;
}

@Injectable({ providedIn: 'root' })
export class WebSocketService {
  /** Socket.IO socket (used in browser/NestJS mode) */
  private socket: Socket | null = null;

  /** One raw WebSocket per agent (Electron / local runtime). */
  private rawWsByAgent = new Map<string, RawWsEntry>();

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

  /** Current reconnection state for the focused agent. */
  reconnectState = signal<string | null>(null);

  /**
   * Per-agent chat UI subscribers. Buffer inbound events when an agent has
   * zero subscribers (e.g. running in background while another chat is open).
   */
  private replaySubscriberCountByAgent = new Map<string, number>();

  /** Per-agent ring buffer for WS payloads when chat UI is not subscribed. */
  private messageBuffers = new Map<string, any[]>();

  private static readonly MAX_BUFFERED_PER_AGENT = 400;

  private reconnectTimers = new Map<string, ReturnType<typeof setTimeout>>();
  private reconnectAttemptsByAgent = new Map<string, number>();
  private reconnectStateByAgent = new Map<string, string>();
  /** Agents that should keep a background WebSocket open. */
  private trackedAgents = new Set<string>();

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
      this.syncConnectedSignal();
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
    this.currentAgentId.set(agentId);
    this.trackedAgents.add(agentId);

    if (this.useRawWs) {
      this.connectRawWs(agentId);
      return;
    }

    this.socket?.emit('join', { agentId });
  }

  /** Close the WebSocket for one agent (others keep running). */
  leaveAgent(agentId: string): void {
    this.trackedAgents.delete(agentId);
    this.clearReconnectForAgent(agentId);
    const entry = this.rawWsByAgent.get(agentId);
    if (entry) {
      entry.ws.onclose = null;
      entry.ws.onerror = null;
      entry.ws.close();
      this.rawWsByAgent.delete(agentId);
    }
    this.messageBuffers.delete(agentId);
    if (this.currentAgentId() === agentId) {
      this.currentAgentId.set(null);
      this.syncConnectedSignal();
    }
  }

  sendMessage(content: string, sessionKey?: string, model?: string): void {
    const payload: any = { type: 'message', content };
    if (sessionKey && sessionKey !== 'websocket:main') {
      payload.session_key = sessionKey;
    }
    if (model) {
      payload.model = model;
    }
    this.sendPayload(payload);
  }

  sendCommand(command: string, args?: Record<string, any>): void {
    this.sendPayload({ type: 'command', command, ...args });
  }

  send(data: Record<string, any>): void {
    this.sendPayload(data);
  }

  private sendPayload(payload: Record<string, any>): void {
    if (this.useRawWs) {
      const ws = this.rawWsForCurrent();
      if (ws?.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(payload));
      }
      return;
    }

    this.socket?.emit('message', payload);
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
      const count = this.replaySubscriberCountByAgent.get(agentId) ?? 0;
      this.replaySubscriberCountByAgent.set(agentId, count + 1);
      const buf = this.messageBuffers.get(agentId);
      if (buf?.length) {
        for (const m of buf) {
          observer.next(m);
        }
        this.messageBuffers.set(agentId, []);
      }
      const sub = this.messagesSubject.subscribe({
        next: (m) => {
          const aid = m?.agent_id ?? this.currentAgentId();
          if (aid === agentId) {
            observer.next(m);
          }
        },
        error: (e) => observer.error(e),
        complete: () => observer.complete(),
      });
      return () => {
        sub.unsubscribe();
        const n = (this.replaySubscriberCountByAgent.get(agentId) ?? 1) - 1;
        if (n <= 0) {
          this.replaySubscriberCountByAgent.delete(agentId);
        } else {
          this.replaySubscriberCountByAgent.set(agentId, n);
        }
      };
    });
  }

  /** Push to subscribers; buffer per agent when no chat replay stream is active. */
  private emitChatMessage(data: any, sourceAgentId?: string): void {
    const aid = data?.agent_id ?? sourceAgentId ?? this.currentAgentId();
    if (aid && !data.agent_id) {
      data = { ...data, agent_id: aid };
    }
    if (aid && (this.replaySubscriberCountByAgent.get(aid) ?? 0) === 0) {
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
    for (const agentId of [...this.rawWsByAgent.keys()]) {
      this.leaveAgent(agentId);
    }

    if (this.socket) {
      this.socket.disconnect();
      this.socket = null;
    }

    this.connected.set(false);
    this.currentAgentId.set(null);
    this.trackedAgents.clear();
    this.messageBuffers.clear();
    this.replaySubscriberCountByAgent.clear();
    this.reconnectState.set(null);
  }

  isAgentConnected(agentId: string): boolean {
    const entry = this.rawWsByAgent.get(agentId);
    return entry?.ws.readyState === WebSocket.OPEN;
  }

  // ─── Raw WebSocket (Electron / local runtime) ─────────────────

  private rawWsForCurrent(): WebSocket | null {
    const agentId = this.currentAgentId();
    if (!agentId) return null;
    return this.rawWsByAgent.get(agentId)?.ws ?? null;
  }

  private syncConnectedSignal(): void {
    const agentId = this.currentAgentId();
    if (!agentId) {
      this.connected.set(false);
      return;
    }
    this.connected.set(this.isAgentConnected(agentId));
    this.reconnectState.set(this.reconnectStateByAgent.get(agentId) ?? null);
  }

  private connectRawWs(agentId: string): void {
    const wsBase = this.runtimeWsUrl || 'ws://127.0.0.1:9222';
    const url = `${wsBase}/ws/chat/${agentId}`;

    const existing = this.rawWsByAgent.get(agentId);
    if (
      existing &&
      existing.url === url &&
      (existing.ws.readyState === WebSocket.OPEN ||
        existing.ws.readyState === WebSocket.CONNECTING)
    ) {
      this.syncConnectedSignal();
      return;
    }

    if (existing) {
      existing.ws.onclose = null;
      existing.ws.onerror = null;
      existing.ws.close();
    }

    const ws = new WebSocket(url);
    this.rawWsByAgent.set(agentId, { ws, url });

    ws.onopen = () => {
      this.syncConnectedSignal();
      this.emitChatMessage(
        {
          type: 'status',
          content: 'Connected to agent',
          agent_id: agentId,
        },
        agentId,
      );
    };

    ws.onmessage = (event: MessageEvent) => {
      try {
        const data = JSON.parse(event.data);
        this.emitChatMessage(data, agentId);
      } catch {
        // Non-JSON message, ignore
      }
    };

    ws.onclose = (ev: CloseEvent) => {
      this.rawWsByAgent.delete(agentId);
      if (this.currentAgentId() === agentId) {
        this.connected.set(false);
      }
      if (ev.code === 4004) {
        this.emitChatMessage(
          {
            type: 'status',
            content: 'Agent no longer exists. Please select or create an agent.',
            agent_id: agentId,
          },
          agentId,
        );
        if (this.currentAgentId() === agentId) {
          this.currentAgentId.set(null);
        }
        return;
      }
      if (!this.trackedAgents.has(agentId)) {
        return;
      }
      this.attemptReconnect(agentId);
    };

    ws.onerror = () => {
      if (!this.reconnectStateByAgent.get(agentId)) {
        this.emitChatMessage(
          {
            type: 'status',
            content: 'WebSocket connection error',
            agent_id: agentId,
          },
          agentId,
        );
      }
    };
  }

  // ─── Auto-reconnect ─────────────────────────────────────────

  private readonly MAX_RECONNECT_ATTEMPTS = 30;

  /** Signal that a restart is expected (call before approving a skill review). */
  expectRestart(): void {
    this.restartExpected.set(true);
  }

  private clearReconnectForAgent(agentId: string): void {
    const timer = this.reconnectTimers.get(agentId);
    if (timer) {
      clearTimeout(timer);
      this.reconnectTimers.delete(agentId);
    }
    this.reconnectAttemptsByAgent.delete(agentId);
    this.reconnectStateByAgent.delete(agentId);
    if (this.currentAgentId() === agentId) {
      this.reconnectState.set(null);
    }
  }

  private attemptReconnect(agentId: string): void {
    if (this.reconnectTimers.has(agentId)) return;

    const state = this.restartExpected() ? 'restarting' : 'reconnecting';
    this.reconnectStateByAgent.set(agentId, state);
    if (this.currentAgentId() === agentId) {
      this.reconnectState.set(state);
    }
    this.reconnectAttemptsByAgent.set(agentId, 0);

    this.emitChatMessage(
      {
        type: 'status',
        content:
          state === 'restarting'
            ? 'Server restarting...'
            : 'Connection lost, reconnecting...',
        agent_id: agentId,
      },
      agentId,
    );

    this.pollAndReconnect(agentId);
  }

  private pollAndReconnect(agentId: string): void {
    const attempts = this.reconnectAttemptsByAgent.get(agentId) ?? 0;
    if (attempts >= this.MAX_RECONNECT_ATTEMPTS) {
      this.reconnectStateByAgent.delete(agentId);
      if (this.currentAgentId() === agentId) {
        this.reconnectState.set(null);
      }
      this.restartExpected.set(false);
      this.emitChatMessage(
        {
          type: 'status',
          content: 'Could not reconnect to server.',
          agent_id: agentId,
        },
        agentId,
      );
      return;
    }

    const delay = Math.min(2000 * Math.pow(1.3, attempts), 10000);
    this.reconnectAttemptsByAgent.set(agentId, attempts + 1);

    const timer = setTimeout(async () => {
      this.reconnectTimers.delete(agentId);

      if (!this.trackedAgents.has(agentId)) {
        this.clearReconnectForAgent(agentId);
        return;
      }

      try {
        const wsBase = this.runtimeWsUrl || 'ws://127.0.0.1:9222';
        const httpBase = wsBase.replace('ws://', 'http://').replace('wss://', 'https://');
        const resp = await fetch(`${httpBase}/health`, { signal: AbortSignal.timeout(5000) });

        if (resp.ok) {
          const wasRestart = this.restartExpected();
          this.reconnectStateByAgent.delete(agentId);
          if (this.currentAgentId() === agentId) {
            this.reconnectState.set(null);
          }
          this.restartExpected.set(false);
          this.connectRawWs(agentId);
          this.emitChatMessage(
            {
              type: 'status',
              content: wasRestart
                ? 'Server restarted — skills loaded. You can continue chatting.'
                : 'Reconnected to server.',
              agent_id: agentId,
            },
            agentId,
          );
          return;
        }
      } catch {
        // Server not ready yet
      }

      this.pollAndReconnect(agentId);
    }, delay);

    this.reconnectTimers.set(agentId, timer);
  }
}
