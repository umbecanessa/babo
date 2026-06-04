import { Injectable, Logger, OnModuleDestroy } from '@nestjs/common';

import { ConfigService } from '@nestjs/config';

import WebSocket from 'ws';

import { ChannelsService } from './channels.service';



const DISCORD_GATEWAY = 'wss://gateway.discord.gg/?v=10&encoding=json';

const INTENTS = 33409; // GUILDS + GUILD_MESSAGES + MESSAGE_CONTENT + GUILD_MEMBERS

const READY_TIMEOUT_MS = 20000;



interface RegisterResult {

  ok: boolean;

  error?: string;

  ready?: boolean;

}



interface GatewaySession {

  ws: WebSocket;

  agentId: string;

  botToken: string;

  heartbeatInterval?: ReturnType<typeof setInterval>;

  readyTimeout?: ReturnType<typeof setTimeout>;

  seq: number | null;

  readyResolve?: (result: RegisterResult) => void;

}



@Injectable()

export class DiscordGatewayService implements OnModuleDestroy {

  private readonly logger = new Logger(DiscordGatewayService.name);

  private sessions = new Map<string, GatewaySession>();

  /** Latest runtime agent registered for each bot token. */
  private agentByToken = new Map<string, string>();

  private tokenByAgent = new Map<string, string>();

  constructor(
    private readonly channels: ChannelsService,
    private readonly config: ConfigService,
  ) {}



  onModuleDestroy() {

    for (const [agentId] of this.sessions) {

      this.unregister(agentId);

    }

  }



  isRegistered(agentId: string): boolean {

    return this.sessions.has(agentId);

  }



  register(agentId: string, botToken: string): Promise<RegisterResult> {

    if (!botToken) {

      return Promise.resolve({ ok: false, error: 'bot_token required' });

    }

    this.unregister(agentId);

    const previousAgent = this.agentByToken.get(botToken);
    if (previousAgent && previousAgent !== agentId) {
      this.logger.warn(
        `Discord GW: bot token reused — unregistering stale agent ${previousAgent}`,
      );
      this.unregister(previousAgent);
    }

    return new Promise((resolve) => {

      try {

        const ws = new WebSocket(DISCORD_GATEWAY);

        const session: GatewaySession = {

          ws,

          agentId,

          botToken,

          seq: null,

          readyResolve: resolve,

        };

        this.sessions.set(agentId, session);



        session.readyTimeout = setTimeout(() => {

          this.logger.warn(`Discord GW READY timeout for ${agentId}`);

          this.finishRegister(session, { ok: false, error: 'Gateway READY timeout' });

          this.cleanupSession(agentId);

        }, READY_TIMEOUT_MS);



        ws.on('open', () => this.logger.log(`Discord GW connecting for ${agentId}`));

        ws.on('message', (raw) => this.handleMessage(session, raw.toString()));

        ws.on('close', (code, reason) => {

          this.logger.warn(`Discord GW closed ${agentId} code=${code} ${reason}`);

          if (session.readyResolve) {

            this.finishRegister(session, {

              ok: false,

              error: `Gateway closed (${code})`,

            });

          }

          this.cleanupSession(agentId);

        });

        ws.on('error', (err) => {

          this.logger.error(`Discord GW error ${agentId}: ${err.message}`);

          if (session.readyResolve) {

            this.finishRegister(session, { ok: false, error: err.message });

          }

        });

      } catch (err: any) {

        resolve({ ok: false, error: err.message });

      }

    });

  }



  unregister(agentId: string): void {

    const session = this.sessions.get(agentId);

    if (!session) return;

    this.cleanupSession(agentId);

  }



  private finishRegister(session: GatewaySession, result: RegisterResult) {

    if (session.readyTimeout) {

      clearTimeout(session.readyTimeout);

      session.readyTimeout = undefined;

    }

    if (session.readyResolve) {

      session.readyResolve(result);

      session.readyResolve = undefined;

    }

  }



  private cleanupSession(agentId: string) {

    const session = this.sessions.get(agentId);

    if (!session) return;

    if (session.heartbeatInterval) {

      clearInterval(session.heartbeatInterval);

    }

    if (session.readyTimeout) {

      clearTimeout(session.readyTimeout);

    }

    try {

      session.ws.close();

    } catch {

      /* ignore */

    }

    const token = this.tokenByAgent.get(agentId);

    if (token && this.agentByToken.get(token) === agentId) {

      this.agentByToken.delete(token);

    }

    this.tokenByAgent.delete(agentId);

    this.sessions.delete(agentId);

  }



  private handleMessage(session: GatewaySession, raw: string) {

    let payload: any;

    try {

      payload = JSON.parse(raw);

    } catch {

      return;

    }

    const { op, t, d, s } = payload;

    if (s != null) session.seq = s;



    switch (op) {

      case 10:

        this.startHeartbeat(session, d.heartbeat_interval);

        this.identify(session);

        break;

      case 11:

        break;

      case 0:

        this.handleDispatch(session, t, d);

        break;

      case 7:

        session.ws.close();

        void this.register(session.agentId, session.botToken);

        break;

      case 9:

        this.logger.error(`Discord GW invalid session ${session.agentId}`);

        this.finishRegister(session, { ok: false, error: 'Invalid session' });

        session.ws.close();

        break;

      default:

        break;

    }

  }



  private startHeartbeat(session: GatewaySession, intervalMs: number) {

    if (session.heartbeatInterval) clearInterval(session.heartbeatInterval);

    session.heartbeatInterval = setInterval(() => {

      if (session.ws.readyState === WebSocket.OPEN) {

        session.ws.send(JSON.stringify({ op: 1, d: session.seq }));

      }

    }, intervalMs);

  }



  private identify(session: GatewaySession) {

    session.ws.send(

      JSON.stringify({

        op: 2,

        d: {

          token: session.botToken,

          intents: INTENTS,

          properties: {

            os: 'linux',

            browser: 'babo',

            device: 'babo',

          },

        },

      }),

    );

  }



  private handleDispatch(session: GatewaySession, eventType: string, data: any) {

    if (eventType === 'READY') {

      this.logger.log(`Discord GW ready for ${session.agentId}`);

      this.agentByToken.set(session.botToken, session.agentId);

      this.tokenByAgent.set(session.agentId, session.botToken);

      this.finishRegister(session, { ok: true, ready: true });

    }



    const forwardTypes = new Set([

      'MESSAGE_CREATE',

      'CHANNEL_CREATE',

      'CHANNEL_UPDATE',

      'GUILD_CREATE',

    ]);

    if (!forwardTypes.has(eventType)) return;



    const envelope = { t: eventType, d: data };

    const pushed = this.channels.pushToRelayByAgentId(

      session.agentId,

      'discord',

      envelope,

    );

    this.logger.debug(

      `Discord GW ${session.agentId} ${eventType} relay=${pushed}`,

    );

  }

}


