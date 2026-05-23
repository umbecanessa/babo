import { Injectable, NotFoundException } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';
import { RuntimeService } from '../runtime/runtime.service';

@Injectable()
export class AdminService {
  constructor(
    private prisma: PrismaService,
    private runtime: RuntimeService,
  ) {}

  // ===================================================================
  // Users
  // ===================================================================

  async listUsers() {
    const users = await this.prisma.user.findMany({
      select: {
        id: true,
        email: true,
        displayName: true,
        role: true,
        createdAt: true,
        updatedAt: true,
        _count: { select: { agents: true, apiKeys: true } },
      },
      orderBy: { createdAt: 'desc' },
    });

    return users.map((u) => ({
      id: u.id,
      email: u.email,
      displayName: u.displayName,
      role: u.role,
      createdAt: u.createdAt,
      updatedAt: u.updatedAt,
      agentCount: u._count.agents,
      apiKeyCount: u._count.apiKeys,
    }));
  }

  async getUser(id: string) {
    const user = await this.prisma.user.findUnique({
      where: { id },
      select: {
        id: true,
        email: true,
        displayName: true,
        role: true,
        createdAt: true,
        updatedAt: true,
        agents: {
          select: {
            id: true,
            runtimeAgentId: true,
            name: true,
            genesisVersion: true,
            status: true,
            createdAt: true,
          },
          orderBy: { createdAt: 'desc' },
        },
        apiKeys: {
          select: {
            id: true,
            keyPrefix: true,
            name: true,
            rateLimitRpm: true,
            isActive: true,
            totalRequests: true,
            lastUsedAt: true,
            createdAt: true,
          },
          orderBy: { createdAt: 'desc' },
        },
      },
    });
    if (!user) throw new NotFoundException('User not found');
    return user;
  }

  async updateUserRole(id: string, role: string) {
    const user = await this.prisma.user.findUnique({ where: { id } });
    if (!user) throw new NotFoundException('User not found');

    return this.prisma.user.update({
      where: { id },
      data: { role },
      select: { id: true, email: true, role: true },
    });
  }

  async deleteUser(id: string) {
    const user = await this.prisma.user.findUnique({
      where: { id },
      include: { agents: true },
    });
    if (!user) throw new NotFoundException('User not found');

    // Delete agents on runtime first
    for (const agent of user.agents) {
      try {
        await this.runtime.deleteAgent(agent.runtimeAgentId);
      } catch {
        // runtime might already have deleted it
      }
    }

    // Cascade delete in DB (agents + apiKeys)
    await this.prisma.user.delete({ where: { id } });
    return { deleted: id };
  }

  // ===================================================================
  // Agents
  // ===================================================================

  async listAllAgents() {
    const agents = await this.prisma.agent.findMany({
      include: {
        user: { select: { email: true, displayName: true } },
      },
      orderBy: { createdAt: 'desc' },
    });

    // Enrich with runtime status
    const enriched = await Promise.all(
      agents.map(async (agent) => {
        try {
          const runtimeStatus = await this.runtime.getAgent(agent.runtimeAgentId);
          return { ...agent, runtime: runtimeStatus };
        } catch {
          return { ...agent, runtime: { status: 'unreachable' } };
        }
      }),
    );

    return enriched;
  }

  async getAgentDetail(id: string) {
    const agent = await this.prisma.agent.findUnique({
      where: { id },
      include: {
        user: { select: { id: true, email: true, displayName: true } },
      },
    });
    if (!agent) throw new NotFoundException('Agent not found');

    try {
      const runtimeStatus = await this.runtime.getAgent(agent.runtimeAgentId);
      return { ...agent, runtime: runtimeStatus };
    } catch {
      return { ...agent, runtime: { status: 'unreachable' } };
    }
  }

  async deleteAgent(id: string) {
    const agent = await this.prisma.agent.findUnique({ where: { id } });
    if (!agent) throw new NotFoundException('Agent not found');

    try {
      await this.runtime.deleteAgent(agent.runtimeAgentId);
    } catch {
      // runtime might already have deleted it
    }

    await this.prisma.agent.delete({ where: { id } });
    return { deleted: id };
  }

  // ===================================================================
  // Runtime proxy methods
  // ===================================================================

  async proxyGetAgentStatus(runtimeAgentId: string) {
    return this.runtime.getAgent(runtimeAgentId);
  }

  async proxyGetAgentChain(runtimeAgentId: string) {
    return this.fetchRuntimeAdmin(`/admin/agents/${runtimeAgentId}/chain`);
  }

  async proxyGetAgentFacts(runtimeAgentId: string, query: Record<string, string>) {
    const params = this.cleanParams(query);
    const qs = params.toString();
    return this.fetchRuntimeAdmin(`/admin/agents/${runtimeAgentId}/facts${qs ? '?' + qs : ''}`);
  }

  async proxyGetAgentEvents(runtimeAgentId: string, query: Record<string, string>) {
    const params = this.cleanParams(query);
    const qs = params.toString();
    return this.fetchRuntimeAdmin(`/admin/agents/${runtimeAgentId}/events${qs ? '?' + qs : ''}`);
  }

  async proxyGetAgentConversation(runtimeAgentId: string) {
    return this.fetchRuntimeAdmin(`/admin/agents/${runtimeAgentId}/conversation`);
  }

  async proxyGetAgentConfig(runtimeAgentId: string) {
    return this.fetchRuntimeAdmin(`/admin/agents/${runtimeAgentId}/config`);
  }

  async proxyGetAgentMemoryTiers(runtimeAgentId: string) {
    return this.fetchRuntimeAdmin(`/admin/agents/${runtimeAgentId}/memory-tiers`);
  }

  async proxyGetHormoneHistory(runtimeAgentId: string) {
    return this.fetchRuntimeAdmin(`/admin/agents/${runtimeAgentId}/hormones/history`);
  }

  async proxyGetSignalHistory(runtimeAgentId: string) {
    return this.fetchRuntimeAdmin(`/admin/agents/${runtimeAgentId}/signals/history`);
  }

  async proxyEvictAgent(runtimeAgentId: string) {
    return this.fetchRuntimeAdmin(`/agents/${runtimeAgentId}/evict`, 'POST');
  }

  async proxyForceSleeep(runtimeAgentId: string) {
    return this.fetchRuntimeAdmin(`/admin/agents/${runtimeAgentId}/sleep`, 'POST');
  }

  async proxyGetSystemHealth() {
    return this.runtime.getHealth();
  }

  async proxyGetAdapterRegistry() {
    return this.fetchRuntimeAdmin('/admin/system/adapters');
  }

  async proxyGetAnalyticsOverview() {
    return this.fetchRuntimeAdmin('/admin/analytics/overview');
  }

  async proxyCompareAgents(ids: string) {
    return this.fetchRuntimeAdmin(`/admin/analytics/agents/compare?ids=${ids}`);
  }

  // ===================================================================
  // Stats
  // ===================================================================

  async getStats() {
    const [userCount, agentCount, apiKeyCount] = await Promise.all([
      this.prisma.user.count(),
      this.prisma.agent.count(),
      this.prisma.apiKey.count(),
    ]);

    let systemHealth: any = null;
    try {
      systemHealth = await this.runtime.getHealth();
    } catch {
      systemHealth = { status: 'unreachable' };
    }

    return {
      users: userCount,
      agents: agentCount,
      apiKeys: apiKeyCount,
      system: systemHealth,
    };
  }

  // ===================================================================
  // Private helpers
  // ===================================================================

  /** Strip empty/undefined values from query params so FastAPI doesn't choke on empty strings for int fields */
  private cleanParams(query: Record<string, string>): URLSearchParams {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== null && value !== '') {
        params.set(key, value);
      }
    }
    return params;
  }

  private async fetchRuntimeAdmin(path: string, method: string = 'GET'): Promise<any> {
    try {
      if (method === 'POST') {
        return await this.runtime.proxyPost(path);
      }
      return await this.runtime.proxyGet(path);
    } catch (err: any) {
      const msg = typeof err.message === 'string' ? err.message : JSON.stringify(err.message);
      throw new NotFoundException(msg || `runtime error`);
    }
  }
}
