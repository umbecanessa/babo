import { Injectable, Logger, Inject, forwardRef } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { Resend } from 'resend';
import { PrismaService } from '../prisma/prisma.service';
import { RuntimeService } from '../runtime/runtime.service';
import { ProviderKeysService } from '../babo-cloud/provider-keys.service';
import WebSocket from 'ws';

@Injectable()
export class ChannelsService {
  private readonly logger = new Logger(ChannelsService.name);
  private resend: Resend | null = null;
  private readonly inboundDomain: string;

  /** Connected relay WebSockets: runtimeAgentId -> WebSocket */
  private relaySockets = new Map<string, WebSocket>();

  constructor(
    private config: ConfigService,
    private prisma: PrismaService,
    private providerKeys: ProviderKeysService,
    @Inject(forwardRef(() => RuntimeService))
    private runtime: RuntimeService,
  ) {
    const apiKey = this.config.get<string>('RESEND_API_KEY');
    this.inboundDomain = this.config.get<string>('RESEND_INBOUND_DOMAIN') || '';
    if (apiKey) {
      this.resend = new Resend(apiKey);
      this.logger.log('Resend SDK initialised');
    } else {
      this.logger.warn('RESEND_API_KEY not set — email channel will be unavailable');
    }
  }

  // ── Relay WebSocket management ──────────────────────────────

  registerRelaySocket(agentId: string, ws: WebSocket): void {
    const existing = this.relaySockets.get(agentId);
    if (existing && existing.readyState === WebSocket.OPEN) {
      existing.close(1000, 'replaced');
    }
    this.relaySockets.set(agentId, ws);
    this.logger.log(`Relay WS registered for agent ${agentId}`);
  }

  hasRelaySocket(agentId: string): boolean {
    const ws = this.relaySockets.get(agentId);
    return !!ws && ws.readyState === WebSocket.OPEN;
  }

  removeRelaySocket(agentId: string): void {
    this.relaySockets.delete(agentId);
    this.logger.log(`Relay WS removed for agent ${agentId}`);
  }

  pushToRelayByAgentId(agentId: string, channel: string, payload: any): boolean {
    const ws = this.relaySockets.get(agentId);
    this.logger.log(
      `pushToRelay: agent=${agentId}, channel=${channel}, ` +
      `hasSocket=${!!ws}, readyState=${ws?.readyState}, ` +
      `registered=[${[...this.relaySockets.keys()].join(', ')}]`,
    );
    if (!ws || ws.readyState !== WebSocket.OPEN) return false;
    try {
      const msg = JSON.stringify({ type: 'channel_message', channel, payload });
      ws.send(msg);
      this.logger.log(`pushToRelay: sent ${msg.length} bytes to ${agentId}`);
      return true;
    } catch (err: any) {
      this.logger.warn(`Relay push failed for ${agentId}: ${err.message}`);
      return false;
    }
  }

  /**
   * Send a chat request through the relay to the desktop runtime.
   * Returns true if the message was successfully sent.
   */
  pushChatToRelay(
    agentId: string,
    content: string,
    sessionKey: string,
    requestId: string,
    channelType = 'web',
  ): boolean {
    const ws = this.relaySockets.get(agentId);
    if (!ws || ws.readyState !== WebSocket.OPEN) return false;
    try {
      const msg = JSON.stringify({
        type: 'chat_request',
        content,
        session_key: sessionKey,
        request_id: requestId,
        channel_type: channelType,
      });
      ws.send(msg);
      this.logger.log(
        `pushChatToRelay: sent chat_request to ${agentId} (req=${requestId})`,
      );
      return true;
    } catch (err: any) {
      this.logger.warn(`Chat relay push failed for ${agentId}: ${err.message}`);
      return false;
    }
  }

  /** Chat response listeners: requestId -> callback */
  private chatResponseCallbacks = new Map<string, (msg: any) => void>();

  /** HTTP-over-relay response listeners: requestId -> callback */
  private httpProxyCallbacks = new Map<string, (msg: any) => void>();

  /** Broadcast listeners: agentId -> Set<callback> */
  private broadcastListeners = new Map<string, Set<(event: any) => void>>();

  onChatResponse(requestId: string, callback: (msg: any) => void): void {
    this.chatResponseCallbacks.set(requestId, callback);
  }

  removeChatResponseCallback(requestId: string): void {
    this.chatResponseCallbacks.delete(requestId);
  }

  addBroadcastListener(agentId: string, callback: (event: any) => void): void {
    if (!this.broadcastListeners.has(agentId)) {
      this.broadcastListeners.set(agentId, new Set());
    }
    this.broadcastListeners.get(agentId)!.add(callback);
  }

  removeBroadcastListener(agentId: string, callback: (event: any) => void): void {
    this.broadcastListeners.get(agentId)?.delete(callback);
  }

  /**
   * Proxy an HTTP request through the relay WebSocket to the desktop runtime.
   * Used when the runtime is behind NAT and unreachable by direct HTTP.
   */
  proxyHttpViaRelay(
    agentId: string,
    method: string,
    path: string,
    body?: any,
    timeoutMs = 30_000,
  ): Promise<any> {
    return new Promise((resolve, reject) => {
      const ws = this.relaySockets.get(agentId);
      if (!ws || ws.readyState !== WebSocket.OPEN) {
        return reject(new Error('Desktop runtime is offline'));
      }

      const requestId = `http_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;

      const timer = setTimeout(() => {
        this.httpProxyCallbacks.delete(requestId);
        reject(new Error('HTTP relay proxy timeout'));
      }, timeoutMs);

      this.httpProxyCallbacks.set(requestId, (resp: any) => {
        clearTimeout(timer);
        if (resp.error) {
          reject(new Error(resp.error));
        } else {
          resolve(resp.body);
        }
      });

      try {
        ws.send(JSON.stringify({
          type: 'http_proxy',
          request_id: requestId,
          method,
          path,
          body,
        }));
      } catch (err: any) {
        clearTimeout(timer);
        this.httpProxyCallbacks.delete(requestId);
        reject(err);
      }
    });
  }

  /** Get all agent IDs with active relay connections. */
  getConnectedRelayAgents(): string[] {
    const result: string[] = [];
    for (const [agentId, ws] of this.relaySockets) {
      if (ws.readyState === WebSocket.OPEN) result.push(agentId);
    }
    return result;
  }

  /**
   * Auto-register an agent in the DB when the relay reports its metadata.
   * Called on relay connect when the Python client sends agent_info.
   */
  async ensureAgentRegistered(agentId: string, info: any): Promise<void> {
    const existing = await this.prisma.agent.findFirst({
      where: { runtimeAgentId: agentId },
    });
    if (existing) {
      if (info.name && !existing.name) {
        await this.prisma.agent.update({
          where: { id: existing.id },
          data: { name: info.name },
        });
      }
      return;
    }

    // Find the user who owns the relay — use the most recent user
    // who created any agent (best-effort for service-to-service context)
    const latestAgent = await this.prisma.agent.findFirst({
      orderBy: { createdAt: 'desc' },
      select: { userId: true },
    });
    if (!latestAgent) return;

    await this.prisma.agent.create({
      data: {
        userId: latestAgent.userId,
        runtimeAgentId: agentId,
        name: info.name || null,
        genesisVersion: info.genesis_version || 'default',
        status: 'alive',
      },
    });
    this.logger.log(`Auto-registered agent ${agentId} (name=${info.name})`);
  }

  /** Called when the relay WS receives a message from the desktop. */
  handleRelayInbound(agentId: string, msg: any): void {
    if (msg.type === 'agent_info') {
      this.ensureAgentRegistered(agentId, msg).catch((err) =>
        this.logger.warn(`Agent auto-register failed: ${err.message}`),
      );
      return;
    }
    if (msg.type === 'chat_response') {
      const cb = this.chatResponseCallbacks.get(msg.request_id);
      if (cb) {
        cb(msg);
        this.chatResponseCallbacks.delete(msg.request_id);
      }
    } else if (msg.type === 'http_proxy_response') {
      const cb = this.httpProxyCallbacks.get(msg.request_id);
      if (cb) {
        cb(msg);
        this.httpProxyCallbacks.delete(msg.request_id);
      }
    } else if (msg.type === 'broadcast') {
      const listeners = this.broadcastListeners.get(agentId);
      if (listeners) {
        const event = msg.event;
        if (event?.type === 'batch_update' && Array.isArray(event.events)) {
          for (const inner of event.events) {
            if (!inner || typeof inner !== 'object') continue;
            for (const cb of listeners) {
              try { cb(inner); } catch { /* ignore */ }
            }
          }
        } else {
          for (const cb of listeners) {
            try { cb(event); } catch { /* ignore */ }
          }
        }
      }
    }
  }

  /**
   * Push a skill install bundle to the desktop runtime via relay WebSocket.
   */
  pushSkillInstall(
    agentId: string,
    slug: string,
    files: Record<string, string>,
  ): boolean {
    const ws = this.relaySockets.get(agentId);
    if (!ws || ws.readyState !== WebSocket.OPEN) return false;
    try {
      const msg = JSON.stringify({
        type: 'skill_install',
        slug,
        files,
      });
      ws.send(msg);
      this.logger.log(`pushSkillInstall: sent ${slug} to ${agentId}`);
      return true;
    } catch (err: any) {
      this.logger.warn(`Skill install push failed for ${agentId}: ${err.message}`);
      return false;
    }
  }

  async queueMessage(
    agentId: string,
    channel: string,
    payload: any,
    alreadyDelivered: boolean,
  ): Promise<void> {
    await this.prisma.pendingChannelMessage.create({
      data: {
        agentId,
        channel,
        payload,
        status: alreadyDelivered ? 'delivered' : 'pending',
        deliveredAt: alreadyDelivered ? new Date() : null,
        attempts: alreadyDelivered ? 1 : 0,
      },
    });
  }

  get isResendConfigured(): boolean {
    return !!this.resend && !!this.inboundDomain;
  }

  private async resendForUser(
    userId: string,
  ): Promise<{ client: Resend; inboundDomain: string } | null> {
    const byo = await this.providerKeys.getResendConfig(userId);
    if (byo?.apiKey && byo.inboundDomain) {
      return { client: new Resend(byo.apiKey), inboundDomain: byo.inboundDomain };
    }
    if (this.resend && this.inboundDomain) {
      return { client: this.resend, inboundDomain: this.inboundDomain };
    }
    return null;
  }

  // ── Alias management ──────────────────────────────────────────

  async activateEmail(
    agentId: string,
    runtimeAgentId: string,
    agentName?: string,
  ): Promise<{ alias: string; from_address: string }> {
    const agent = await this.prisma.agent.findUnique({ where: { id: agentId } });
    if (!agent) {
      throw new Error('Agent not found');
    }
    const resendCfg = await this.resendForUser(agent.userId);
    if (!resendCfg) {
      throw new Error(
        'Email is not configured — set Babo Resend on the server or add your Resend API key in settings',
      );
    }
    const inboundDomain = resendCfg.inboundDomain;

    // Check if this agent already has an email alias
    const existing = await this.prisma.channelAlias.findFirst({
      where: { agentId, channel: 'email' },
    });
    if (existing) {
      return { alias: existing.alias, from_address: existing.displayName || existing.alias };
    }

    const slug = this.slugify(agentName || 'agent');
    const hex = this.randomHex(4);
    const alias = `${slug}.${hex}@${inboundDomain}`;
    const from_address = `${agentName || 'Agent'} <${alias}>`;

    // Store in DB for webhook routing
    await this.prisma.channelAlias.create({
      data: {
        agentId,
        channel: 'email',
        alias,
        displayName: from_address,
      },
    });

    // Notify Python runtime so it updates skill config
    try {
      await this.runtime.proxyPost(`/skills/email-channel/activate/${runtimeAgentId}`, {
        alias,
        from_address,
        provisioned_by: 'nestjs',
      });
    } catch (err: any) {
      this.logger.warn(`Failed to notify runtime of email activation: ${err.message}`);
    }

    return { alias, from_address };
  }

  /**
   * Resolve an email alias to the owning agent.
   * Returns { agentId, runtimeAgentId } or null.
   */
  async resolveAlias(alias: string): Promise<{ agentId: string; runtimeAgentId: string } | null> {
    const record = await this.prisma.channelAlias.findUnique({
      where: { alias },
      include: { agent: true },
    });
    if (!record) return null;
    return {
      agentId: record.agentId,
      runtimeAgentId: record.agent.runtimeAgentId,
    };
  }

  // ── Send email ────────────────────────────────────────────────

  async sendEmail(
    params: {
      from: string;
      to: string | string[];
      subject: string;
      html?: string;
      text?: string;
      reply_to?: string;
      in_reply_to?: string;
      references?: string;
    },
    userId?: string,
  ): Promise<{ id: string }> {
    let resendClient = this.resend;
    if (userId) {
      const cfg = await this.resendForUser(userId);
      if (cfg) resendClient = cfg.client;
    }
    if (!resendClient) {
      throw new Error('Resend is not configured');
    }

    const payload: any = {
      from: params.from,
      to: Array.isArray(params.to) ? params.to : [params.to],
      subject: params.subject,
    };
    if (params.html) payload.html = params.html;
    if (params.text) payload.text = params.text;
    if (params.reply_to) payload.reply_to = params.reply_to;
    if (params.in_reply_to) {
      payload.headers = {
        'In-Reply-To': params.in_reply_to,
        References: params.references || params.in_reply_to,
      };
    }

    const result = await resendClient.emails.send(payload);
    if (result.error) {
      throw new Error(result.error.message || 'Resend send failed');
    }
    return { id: result.data?.id || '' };
  }

  // ── Inbound email processing ──────────────────────────────────

  async fetchInboundEmail(emailId: string): Promise<any> {
    const apiKey = this.config.get<string>('RESEND_API_KEY');
    if (!apiKey) throw new Error('Resend is not configured');

    const res = await fetch(`https://api.resend.com/emails/receiving/${emailId}`, {
      headers: { Authorization: `Bearer ${apiKey}` },
    });
    if (!res.ok) throw new Error(`Resend fetch failed: ${res.status}`);
    return res.json();
  }

  /**
   * Queue an inbound message for delivery to the agent's runtime.
   * Also tries immediate relay delivery via WebSocket.
   */
  async queueAndDeliver(
    agentId: string,
    runtimeAgentId: string,
    channel: string,
    payload: any,
  ): Promise<{ queued: boolean; delivered: boolean }> {
    const delivered = this.pushToRelayByAgentId(runtimeAgentId, channel, payload);
    await this.queueMessage(agentId, channel, payload, delivered);
    if (!delivered) {
      this.logger.warn(
        `No relay WS for ${channel}/${runtimeAgentId} — message queued`,
      );
    }
    return { queued: true, delivered };
  }

  /**
   * On relay WS connect, drain any pending messages and push them through.
   */
  async drainAndPushPending(runtimeAgentId: string, ws: import('ws').WebSocket): Promise<void> {
    const agent = await this.prisma.agent.findFirst({
      where: { runtimeAgentId: runtimeAgentId },
      select: { id: true },
    });
    if (!agent) return;

    const pending = await this.prisma.pendingChannelMessage.findMany({
      where: { agentId: agent.id, status: 'pending' },
      orderBy: { createdAt: 'asc' },
      take: 100,
    });

    if (!pending.length) return;

    const deliveredIds: string[] = [];
    for (const msg of pending) {
      try {
        ws.send(JSON.stringify({
          type: 'channel_message',
          channel: msg.channel,
          payload: msg.payload,
        }));
        deliveredIds.push(msg.id);
      } catch {
        break;
      }
    }

    if (deliveredIds.length) {
      await this.prisma.pendingChannelMessage.updateMany({
        where: { id: { in: deliveredIds } },
        data: { status: 'delivered', deliveredAt: new Date() },
      });
      this.logger.log(
        `Relay WS ${runtimeAgentId}: drained ${deliveredIds.length} pending messages`,
      );
    }
  }

  // ── Pending message drain (called by runtime or frontend) ────

  async drainPending(agentId: string): Promise<any[]> {
    const pending = await this.prisma.pendingChannelMessage.findMany({
      where: { agentId, status: 'pending' },
      orderBy: { createdAt: 'asc' },
      take: 50,
    });

    if (!pending.length) return [];

    // Mark as delivered
    await this.prisma.pendingChannelMessage.updateMany({
      where: { id: { in: pending.map((m) => m.id) } },
      data: { status: 'delivered', deliveredAt: new Date() },
    });

    return pending.map((m) => ({
      id: m.id,
      channel: m.channel,
      payload: m.payload,
      createdAt: m.createdAt,
    }));
  }

  // ── Status (proxy to runtime, fallback to DB) ───────────────────

  /** Get email alias for an agent from DB (for status fallback when runtime is unreachable). */
  async getEmailAliasForAgent(agentId: string): Promise<string> {
    const record = await this.prisma.channelAlias.findFirst({
      where: { agentId, channel: 'email' },
    });
    return record?.alias ?? '';
  }

  async getEmailStatus(runtimeAgentId: string, nestjsAgentId?: string): Promise<any> {
    try {
      const runtimeStatus = await this.runtime.proxyGet(`/skills/email-channel/status/${runtimeAgentId}`);
      if (runtimeStatus?.alias) return runtimeStatus;
      // Runtime reachable but returned no alias -- check DB as well
      // (alias may have been written to a different data dir)
      if (nestjsAgentId) {
        const dbAlias = await this.getEmailAliasForAgent(nestjsAgentId);
        if (dbAlias) {
          return { ...runtimeStatus, connected: true, enabled: true, alias: dbAlias };
        }
      }
      return runtimeStatus;
    } catch {
      const alias = nestjsAgentId
        ? await this.getEmailAliasForAgent(nestjsAgentId)
        : '';
      return {
        channel: 'email',
        connected: !!alias,
        enabled: !!alias,
        alias,
      };
    }
  }

  // ── Helpers ───────────────────────────────────────────────────

  private slugify(name: string): string {
    const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
    return slug.slice(0, 20) || 'agent';
  }

  private randomHex(bytes: number): string {
    const arr = new Uint8Array(bytes);
    crypto.getRandomValues(arr);
    return Array.from(arr, (b) => b.toString(16).padStart(2, '0')).join('');
  }
}
