import {
  WebSocketGateway,
  WebSocketServer,
  SubscribeMessage,
  OnGatewayConnection,
  OnGatewayDisconnect,
  ConnectedSocket,
  MessageBody,
} from '@nestjs/websockets';
import { Logger, UnauthorizedException } from '@nestjs/common';
import { JwtService } from '@nestjs/jwt';
import { ConfigService } from '@nestjs/config';
import { Server, Socket } from 'socket.io';
import { RuntimeService } from '../runtime/runtime.service';
import { AgentsService } from '../agents/agents.service';
import { ChannelsService } from '../channels/channels.service';
import { randomUUID } from 'crypto';

@WebSocketGateway({
  namespace: '/chat',
  cors: { origin: '*' },
})
export class ChatGateway implements OnGatewayConnection, OnGatewayDisconnect {
  @WebSocketServer() server!: Server;
  private readonly logger = new Logger(ChatGateway.name);

  constructor(
    private jwt: JwtService,
    private config: ConfigService,
    private runtime: RuntimeService,
    private agents: AgentsService,
    private channels: ChannelsService,
  ) {}

  private emitRuntime(client: Socket, payload: unknown): void {
    client.emit('runtime', payload);
  }

  private emitRuntimeDisconnected(client: Socket): void {
    client.emit('runtime_disconnected');
  }

  private getRuntimeAgentId(client: Socket): string | undefined {
    return client.data.runtimeAgentId;
  }

  private setRuntimeAgentId(client: Socket, runtimeAgentId: string): void {
    client.data.runtimeAgentId = runtimeAgentId;
  }

  async handleConnection(client: Socket) {
    try {
      const token =
        client.handshake.auth?.token ||
        client.handshake.headers?.authorization?.replace('Bearer ', '');

      if (!token) throw new UnauthorizedException('No token');

      const payload = this.jwt.verify(token, {
        secret: this.config.get('JWT_SECRET'),
      });

      client.data.userId = payload.sub;
      client.data.email = payload.email;
      client.data.displayName = payload.name || '';

      this.logger.log(`Client connected: ${client.id} (${payload.email})`);
    } catch (err: any) {
      this.logger.warn(`Auth failed for ${client.id}: ${err.message}`);
      client.emit('error', { message: 'Authentication failed' });
      client.disconnect();
    }
  }

  handleDisconnect(client: Socket) {
    this.logger.log(`Client disconnected: ${client.id}`);
    this.runtime.disconnectChat(client.id);

    const runtimeAgentId = this.getRuntimeAgentId(client);
    if (runtimeAgentId && client.data.broadcastListener) {
      this.channels.removeBroadcastListener(
        runtimeAgentId,
        client.data.broadcastListener,
      );
    }
  }

  @SubscribeMessage('join')
  async handleJoin(
    @ConnectedSocket() client: Socket,
    @MessageBody() data: { agentId: string },
  ) {
    try {
      if (this.getRuntimeAgentId(client)) {
        this.runtime.disconnectChat(client.id);
      }

      const runtimeAgentId = await this.agents.getRuntimeAgentId(
        client.data.userId,
        data.agentId,
      );

      const hasRelay = this.channels.hasRelaySocket(runtimeAgentId);

      if (hasRelay) {
        client.data.relayMode = true;
        this.logger.log(`Client ${client.id} joined agent ${runtimeAgentId} via RELAY`);

        const listener = (event: any) => {
          this.emitRuntime(client, event);
        };
        client.data.broadcastListener = listener;
        this.channels.addBroadcastListener(runtimeAgentId, listener);

        this.bootstrapRelayClient(client, runtimeAgentId);
      } else {
        client.data.relayMode = false;
        this.runtime.connectChat(
          client.id,
          runtimeAgentId,
          (msg) => {
            if (msg.type === 'name_update' && msg.name) {
              this.agents
                .updateName(client.data.userId, data.agentId, msg.name)
                .catch((err: any) =>
                  this.logger.warn(`Failed to persist agent name: ${err.message}`),
                );
            }
            this.emitRuntime(client, msg);
          },
          () => this.emitRuntimeDisconnected(client),
        );
      }

      client.data.agentId = data.agentId;
      this.setRuntimeAgentId(client, runtimeAgentId);

      if (client.data.email) {
        this.agents
          .syncOwnerIdentity(runtimeAgentId, client.data.email, client.data.displayName)
          .catch(() => {});
      }

      client.emit('joined', {
        agentId: data.agentId,
        runtimeAgentId,
      });
    } catch (err: any) {
      client.emit('error', { message: err.message });
    }
  }

  @SubscribeMessage('message')
  handleMessage(
    @ConnectedSocket() client: Socket,
    @MessageBody() data: { type?: string; content?: string; command?: string; model?: string; sessionKey?: string },
  ) {
    this.logger.log(
      `Message from ${client.id}: type=${data?.type || 'chat'}, content=${(data?.content || '').substring(0, 80)}`,
    );

    const runtimeAgentId = this.getRuntimeAgentId(client);
    if (!runtimeAgentId) {
      this.logger.warn(`Client ${client.id} not joined to agent, rejecting message`);
      client.emit('error', { message: 'Not joined to an agent. Send "join" first.' });
      return;
    }

    if (client.data.relayMode) {
      const sessionKey = `web:remote:${client.id}`;
      const requestId = randomUUID();

      const sent = this.channels.pushChatToRelay(
        runtimeAgentId,
        data.content || '',
        sessionKey,
        requestId,
      );

      if (!sent) {
        client.emit('error', {
          message: 'Agent desktop is not connected. Please ensure the desktop app is running.',
        });
        return;
      }

      this.channels.onChatResponse(requestId, (msg) => {
        this.emitRuntime(client, {
          type: 'response_end',
          response: msg.content || '',
          error: msg.error || undefined,
          nls: msg.nls || undefined,
        });
      });

      setTimeout(() => {
        this.channels.removeChatResponseCallback(requestId);
      }, 120_000);

      this.logger.log(`Relayed to desktop for agent ${runtimeAgentId}`);
      return;
    }

    if (data.type === 'command') {
      this.runtime.sendMessage(client.id, {
        type: 'command',
        command: data.command,
      });
      return;
    }

    const payload: Record<string, unknown> = {
      type: 'message',
      content: data.content,
      channel_type: 'web',
    };
    if (data.model) {
      payload.model = data.model;
    }
    if (data.sessionKey) {
      payload.session_key = data.sessionKey;
    }
    this.runtime.sendMessage(client.id, payload);
    this.logger.log(`Forwarded to runtime for agent ${runtimeAgentId}`);
  }

  @SubscribeMessage('remote_chat')
  async handleRemoteChat(
    @ConnectedSocket() client: Socket,
    @MessageBody() data: { content: string; sessionKey?: string },
  ) {
    const runtimeAgentId = this.getRuntimeAgentId(client);
    if (!runtimeAgentId) {
      client.emit('error', { message: 'Not joined to an agent. Send "join" first.' });
      return;
    }

    const sessionKey = data.sessionKey || `web:remote:${client.id}`;
    const requestId = randomUUID();

    const sent = this.channels.pushChatToRelay(
      runtimeAgentId,
      data.content,
      sessionKey,
      requestId,
    );

    if (!sent) {
      client.emit('error', {
        message: 'Agent desktop is not connected. Please ensure the desktop app is running.',
      });
      return;
    }

    this.channels.onChatResponse(requestId, (msg) => {
      this.emitRuntime(client, {
        type: 'response_end',
        response: msg.content || '',
        error: msg.error || undefined,
      });
    });

    setTimeout(() => {
      this.channels.removeChatResponseCallback(requestId);
    }, 120_000);
  }

  @SubscribeMessage('subscribe_broadcasts')
  handleSubscribeBroadcasts(@ConnectedSocket() client: Socket) {
    const runtimeAgentId = this.getRuntimeAgentId(client);
    if (!runtimeAgentId) return;

    const listener = (event: any) => {
      this.emitRuntime(client, event);
    };

    client.data.broadcastListener = listener;
    this.channels.addBroadcastListener(runtimeAgentId, listener);
  }

  private async bootstrapRelayClient(client: Socket, runtimeAgentId: string): Promise<void> {
    try {
      const s = await this.channels.proxyHttpViaRelay(
        runtimeAgentId, 'GET', `/agents/${runtimeAgentId}`,
      );
      if (s) {
        this.emitRuntime(client, {
          type: 'status',
          agent_status: s.status || 'alive',
          agent_name: s.name || null,
          facts_in_memory: s.facts_in_memory ?? 0,
          turn_count: s.turn_count ?? 0,
          sleep_count: s.sleep_count ?? 0,
          hormones: s.hormones ?? {},
          ans: s.ans ?? {},
          heartbeat: s.heartbeat ?? {},
          working_memory: s.working_memory ?? {},
          narrative: s.narrative ?? {},
          theory_of_mind: s.theory_of_mind ?? {},
          predictive_processing: s.predictive_processing ?? {},
          network_dynamics: s.network_dynamics ?? {},
        });
      }
    } catch (err: any) {
      this.logger.warn(`Bootstrap status failed for ${runtimeAgentId}: ${err.message}`);
    }

    try {
      const conv = await this.channels.proxyHttpViaRelay(
        runtimeAgentId, 'GET', `/admin/agents/${runtimeAgentId}/conversation`,
      );
      if (conv?.messages?.length) {
        const chatMessages = conv.messages.filter(
          (m: any) => m.role === 'user' || m.role === 'assistant',
        );
        if (chatMessages.length) {
          this.emitRuntime(client, { type: 'history', messages: chatMessages });
        }
      }
    } catch (err: any) {
      this.logger.warn(`Bootstrap history failed for ${runtimeAgentId}: ${err.message}`);
    }
  }
}
