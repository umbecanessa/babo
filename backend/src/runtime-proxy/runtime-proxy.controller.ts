import {
  All,
  Controller,
  Req,
  Res,
  UseGuards,
  Logger,
  ForbiddenException,
  NotFoundException,
} from '@nestjs/common';
import { JwtAuthGuard } from '../auth/jwt-auth.guard';
import { PrismaService } from '../prisma/prisma.service';
import { ChannelsService } from '../channels/channels.service';
/**
 * Catch-all runtime proxy for browser/remote clients.
 *
 * In Electron mode the Angular frontend talks directly to the local
 * Python runtime (http://127.0.0.1:9222).  In browser mode, these
 * same URLs land here at /api/rt/* and are forwarded to the desktop
 * Python runtime via the relay WebSocket.
 *
 * Path translation:
 *   Browser sends:  GET /api/rt/admin/agents/{dbId}/chain?page=1
 *   Proxy sends:    GET /admin/agents/{runtimeAgentId}/chain?page=1
 *   (via relay WS to desktop Python runtime)
 */
@Controller('rt')
@UseGuards(JwtAuthGuard)
export class RuntimeProxyController {
  private readonly logger = new Logger(RuntimeProxyController.name);

  constructor(
    private prisma: PrismaService,
    private channels: ChannelsService,
  ) {}

  /**
   * Resolve an agent ID (NestJS DB UUID or runtimeAgentId) to runtimeAgentId,
   * verifying ownership by the requesting user.
   */
  private async resolveAgentId(userId: string, rawId: string): Promise<string> {
    let agent = await this.prisma.agent.findUnique({ where: { id: rawId } });
    if (!agent) {
      agent = await this.prisma.agent.findFirst({ where: { runtimeAgentId: rawId } });
    }
    if (!agent) throw new NotFoundException(`Agent ${rawId} not found`);
    if (agent.userId !== userId) throw new ForbiddenException();
    return agent.runtimeAgentId;
  }

  /**
   * Pick any connected relay agent for global (non-agent-specific) requests
   * like /admin/tools/catalog or /health.
   */
  private getAnyRelayAgent(): string {
    const agents = this.channels.getConnectedRelayAgents();
    if (agents.length === 0) {
      throw new NotFoundException('No desktop runtime is currently online');
    }
    return agents[0];
  }

  @All('{*path}')
  async proxy(@Req() req: any, @Res() res: any) {
    const userId = req.user?.userId;

    // Strip /api/rt prefix to recover the Python-compatible path
    const fullUrl: string = req.originalUrl;
    const rtIndex = fullUrl.indexOf('/rt/');
    const pythonPath = rtIndex >= 0 ? fullUrl.substring(rtIndex + 3) : fullUrl;

    // If the path references an agent, translate DB UUID → runtimeAgentId
    const agentMatch = pythonPath.match(
      /\/(admin\/)?agents\/([0-9a-f-]{8,36})(\/|$|\?)/,
    );

    let relayAgentId: string;
    let proxyPath = pythonPath;

    if (agentMatch) {
      const rawId = agentMatch[2];
      try {
        const runtimeId = await this.resolveAgentId(userId, rawId);
        relayAgentId = runtimeId;
        if (rawId !== runtimeId) {
          proxyPath = pythonPath.replace(rawId, runtimeId);
        }
      } catch (err) {
        if (err instanceof NotFoundException || err instanceof ForbiddenException) {
          throw err;
        }
        relayAgentId = this.getAnyRelayAgent();
      }
    } else {
      relayAgentId = this.getAnyRelayAgent();
    }

    const method = req.method;
    const body =
      method !== 'GET' && method !== 'HEAD' && req.body && Object.keys(req.body).length > 0
        ? req.body
        : undefined;

    this.logger.debug(`Proxy ${method} ${proxyPath} → relay ${relayAgentId}`);

    try {
      const result = await this.channels.proxyHttpViaRelay(
        relayAgentId,
        method,
        proxyPath,
        body,
      );
      return res.json(result);
    } catch (err: any) {
      this.logger.warn(`Proxy failed: ${err.message}`);
      return res.status(502).json({
        error: 'Desktop runtime unreachable',
        detail: err.message,
      });
    }
  }
}
