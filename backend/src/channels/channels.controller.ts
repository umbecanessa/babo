import {
  Controller, Get, Post, Body, Param, Headers,
  UseGuards, Request, HttpException, HttpStatus,
} from '@nestjs/common';
import { JwtAuthGuard } from '../auth/jwt-auth.guard';
import { ConfigService } from '@nestjs/config';
import { AgentsService } from '../agents/agents.service';
import { ChannelsService } from './channels.service';

@Controller('channels')
export class ChannelsController {
  constructor(
    private channels: ChannelsService,
    private agents: AgentsService,
    private config: ConfigService,
  ) {}

  // ── Email: Activate (user-facing, JWT-protected) ───────────────

  @Post('email/activate/:agentId')
  @UseGuards(JwtAuthGuard)
  async activateEmail(
    @Request() req: any,
    @Param('agentId') agentId: string,
  ) {
    const available = await this.channels.isEmailAvailableForUser(req.user.userId);
    if (!available) {
      throw new HttpException(
        'Email is not configured — add your Resend API key in Settings → Integrations, '
          + 'or set RESEND_API_KEY and RESEND_INBOUND_DOMAIN on your NestJS server.',
        HttpStatus.SERVICE_UNAVAILABLE,
      );
    }

    const agent = await this.resolveAgent(req.user.userId, agentId);
    const result = await this.channels.activateEmail(
      agent.id,
      agent.runtimeAgentId,
      agent.name || 'agent',
    );
    return result;
  }

  // ── Email: Send (called by Python runtime, secret-protected) ───

  @Post('email/send')
  async sendEmail(
    @Headers('x-runtime-secret') secret: string,
    @Body() body: {
      from: string;
      to: string | string[];
      subject: string;
      html?: string;
      text?: string;
      reply_to?: string;
      in_reply_to?: string;
      references?: string;
    },
  ) {
    this.validateRuntimeSecret(secret);
    return this.channels.sendEmail(body);
  }

  // ── Generic Channel Webhook Relay ────────────────────────────
  //
  // External services (Telegram, WhatsApp, etc.) POST here.
  // NestJS queues the payload and attempts immediate delivery to the
  // desktop runtime via the existing HTTP proxy.  If the runtime is
  // unreachable (NAT / offline), the payload stays in the pending
  // queue for the desktop to drain later.

  @Post('webhook/:channel/:agentId')
  async channelWebhookRelay(
    @Param('channel') channel: string,
    @Param('agentId') agentId: string,
    @Body() payload: any,
  ) {
    console.log(`[webhook-relay] ${channel}/${agentId} — incoming`);

    // Try relay WS push first (works even if agent isn't in NestJS DB,
    // which is the case for desktop-mode agents created on the Python runtime).
    const pushed = this.channels.pushToRelayByAgentId(agentId, channel, payload);
    console.log(`[webhook-relay] ${channel}/${agentId} — relay push: ${pushed}`);

    // Also try to queue in DB for persistence/fallback
    let queued = false;
    const agent = await this.agents.findOneByRuntimeAgentIdUnsafe(agentId);
    if (agent) {
      try {
        await this.channels.queueMessage(agent.id, channel, payload, pushed);
        queued = true;
      } catch (err: any) {
        console.log(`[webhook-relay] ${channel}/${agentId} — queue error: ${err.message}`);
      }
    }

    return { ok: true, delivered: pushed, queued };
  }

  // ── Email: Inbound Webhook (called by Resend, no auth) ────────
  //
  // SINGLE endpoint for ALL agents. Resend configures this as the
  // inbound webhook for the entire domain. We resolve the target
  // agent by looking up the "to" address in the channel_aliases table.

  @Post('email/webhook')
  async inboundWebhook(@Body() payload: any) {
    const data = payload?.data || payload;

    // Extract recipient alias from the "to" field
    const toList: string[] = Array.isArray(data.to) ? data.to : [data.to || ''];
    const alias = toList.find((addr: string) => addr.includes('@')) || '';

    if (!alias) {
      return { ok: false, reason: 'no recipient address' };
    }

    // Clean alias (strip display name if present: "Name <email>" → "email")
    const cleanAlias = alias.includes('<')
      ? alias.match(/<([^>]+)>/)?.[1] || alias
      : alias;

    // Look up which agent owns this alias
    const resolved = await this.channels.resolveAlias(cleanAlias.toLowerCase().trim());
    if (!resolved) {
      return { ok: false, reason: `no agent found for alias: ${cleanAlias}` };
    }

    // Fetch full email content from Resend if we have an email_id
    let enrichedPayload = payload;
    const emailId = data.email_id;
    if (emailId) {
      try {
        const fullEmail = await this.channels.fetchInboundEmailForAgent(
          emailId,
          resolved.agentId,
        );
        enrichedPayload = { ...payload, _full_email: fullEmail };
      } catch {
        // Continue with partial payload
      }
    }

    // Queue and attempt immediate delivery
    const result = await this.channels.queueAndDeliver(
      resolved.agentId,
      resolved.runtimeAgentId,
      'email',
      enrichedPayload,
    );

    return { ok: true, ...result };
  }

  // ── Email: Status (user-facing, JWT-protected) ─────────────────

  @Get('email/status/:agentId')
  @UseGuards(JwtAuthGuard)
  async emailStatus(
    @Request() req: any,
    @Param('agentId') agentId: string,
  ) {
    const agent = await this.resolveAgent(req.user.userId, agentId);

    return this.channels.getEmailStatus(agent.runtimeAgentId, agent.id);
  }

  // ── Pending Messages: Drain (called by runtime or frontend) ────
  //
  // The local Python runtime (or Electron frontend) polls this
  // endpoint to pick up inbound emails that couldn't be delivered
  // in real-time (e.g. because the runtime was behind NAT).

  @Get('pending/:agentId')
  @UseGuards(JwtAuthGuard)
  async drainPending(
    @Request() req: any,
    @Param('agentId') agentId: string,
  ) {
    const agent = await this.resolveAgent(req.user.userId, agentId);
    const messages = await this.channels.drainPending(agent.id);
    return { messages };
  }

  // Also allow the Python runtime to drain via secret (no JWT).
  // The runtime sends its Python agent ID, so we resolve it to the
  // NestJS agent UUID before querying the pending queue.
  @Post('pending/drain')
  async drainPendingRuntime(
    @Headers('x-runtime-secret') secret: string,
    @Body() body: { agent_id: string },
  ) {
    this.validateRuntimeSecret(secret);
    const nestjsAgentId = await this.agents.findByRuntimeAgentId(body.agent_id);
    if (!nestjsAgentId) {
      return { messages: [] };
    }
    const messages = await this.channels.drainPending(nestjsAgentId);
    return { messages };
  }

  // ── Internal: Soul-package sync from runtime ─────────────────

  @Post('internal/agents/:agentId/soul-packages')
  async internalCreateSoulPackage(
    @Param('agentId') agentId: string,
    @Headers('authorization') auth: string,
    @Body() body: { chainHeight?: number; metadata?: any },
  ) {
    this.validateRuntimeSecret((auth || '').replace('Bearer ', ''));
    return this.agents.createSoulPackageInternal(agentId, body);
  }

  // ── Helpers ────────────────────────────────────────────────────

  /**
   * Resolve an agent by NestJS UUID, runtime agent ID, or auto-register it.
   * In Electron mode agents are created directly on the Python runtime
   * bypassing NestJS, so the DB record may not exist yet.
   */
  private async resolveAgent(userId: string, agentId: string) {
    try {
      return await this.agents.findOne(userId, agentId);
    } catch {
      try {
        return await this.agents.findOneByRuntimeAgentId(userId, agentId);
      } catch {
        return await this.agents.ensureByRuntimeAgentId(userId, agentId);
      }
    }
  }

  private validateRuntimeSecret(secret: string): void {
    const expected = this.config.get<string>('RUNTIME_SHARED_SECRET') || '';
    if (!expected || secret !== expected) {
      throw new HttpException('Unauthorized', HttpStatus.UNAUTHORIZED);
    }
  }
}
