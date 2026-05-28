import { Injectable, NotFoundException, ForbiddenException, ConflictException } from '@nestjs/common';
import * as bcrypt from 'bcrypt';
import { PrismaService } from '../prisma/prisma.service';
import { RuntimeService } from '../runtime/runtime.service';
import { EntitlementsService } from '../babo-cloud/entitlements.service';

@Injectable()
export class AdminService {
  constructor(
    private prisma: PrismaService,
    private runtime: RuntimeService,
    private entitlements: EntitlementsService,
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
  // First-run setup
  // ===================================================================

  async getSetupStatus() {
    const [adminCount, userCount] = await Promise.all([
      this.prisma.user.count({ where: { role: 'admin' } }),
      this.prisma.user.count(),
    ]);
    return {
      needsSetup: adminCount === 0,
      hasAdmin: adminCount > 0,
      userCount,
    };
  }

  async bootstrapFirstAdmin(
    email: string,
    password: string,
    displayName?: string,
  ) {
    const adminCount = await this.prisma.user.count({ where: { role: 'admin' } });
    if (adminCount > 0) {
      throw new ForbiddenException('An admin account already exists');
    }

    const existing = await this.prisma.user.findUnique({ where: { email } });
    if (existing) {
      throw new ConflictException(
        'Email already registered — log in and promote via another admin, or use a different email',
      );
    }

    const passwordHash = await bcrypt.hash(password, 12);
    const user = await this.prisma.user.create({
      data: {
        email,
        passwordHash,
        displayName,
        role: 'admin',
      },
    });
    await this.entitlements.ensureSubscriptionForUser(user.id);
    return user;
  }

  // ===================================================================
  // Inference usage (Babo Cloud ledger)
  // ===================================================================

  async getUsageOverview(limit = 50) {
    const take = Math.min(Math.max(limit, 1), 200);
    const [recent, totals, byUserRaw] = await Promise.all([
      this.prisma.inferenceUsage.findMany({
        orderBy: { createdAt: 'desc' },
        take,
        select: {
          id: true,
          userId: true,
          agentId: true,
          model: true,
          provider: true,
          placement: true,
          route: true,
          workload: true,
          promptTokens: true,
          completionTokens: true,
          totalTokens: true,
          upstreamCostCents: true,
          createdAt: true,
        },
      }),
      this.prisma.inferenceUsage.aggregate({
        _sum: {
          promptTokens: true,
          completionTokens: true,
          totalTokens: true,
          upstreamCostCents: true,
        },
        _count: true,
      }),
      this.prisma.inferenceUsage.groupBy({
        by: ['userId'],
        _sum: {
          promptTokens: true,
          completionTokens: true,
          totalTokens: true,
          upstreamCostCents: true,
        },
        _count: { _all: true },
        orderBy: { _sum: { totalTokens: 'desc' } },
      }),
    ]);

    const userIds = [...new Set([
      ...recent.map((r) => r.userId),
      ...byUserRaw.map((r) => r.userId),
    ])];
    const users = await this.prisma.user.findMany({
      where: { id: { in: userIds } },
      select: { id: true, email: true, displayName: true },
    });
    const userMap = new Map(users.map((u) => [u.id, u]));

    return {
      totals: {
        requestCount: totals._count,
        promptTokens: totals._sum.promptTokens ?? 0,
        completionTokens: totals._sum.completionTokens ?? 0,
        totalTokens: totals._sum.totalTokens ?? 0,
        upstreamCostCents: totals._sum.upstreamCostCents ?? 0,
      },
      byUser: byUserRaw.map((row) => ({
        userId: row.userId,
        email: userMap.get(row.userId)?.email ?? null,
        displayName: userMap.get(row.userId)?.displayName ?? null,
        requestCount: row._count._all,
        promptTokens: row._sum.promptTokens ?? 0,
        completionTokens: row._sum.completionTokens ?? 0,
        totalTokens: row._sum.totalTokens ?? 0,
        upstreamCostCents: row._sum.upstreamCostCents ?? 0,
      })),
      recent: recent.map((row) => ({
        ...row,
        userEmail: userMap.get(row.userId)?.email ?? null,
      })),
    };
  }

  async getUserUsage(userId: string, limit = 50) {
    const user = await this.prisma.user.findUnique({
      where: { id: userId },
      select: {
        id: true,
        email: true,
        displayName: true,
        role: true,
        cloudSubscription: true,
      },
    });
    if (!user) throw new NotFoundException('User not found');

    const take = Math.min(Math.max(limit, 1), 200);
    const [recent, totals] = await Promise.all([
      this.prisma.inferenceUsage.findMany({
        where: { userId },
        orderBy: { createdAt: 'desc' },
        take,
      }),
      this.prisma.inferenceUsage.aggregate({
        where: { userId },
        _sum: {
          promptTokens: true,
          completionTokens: true,
          totalTokens: true,
          upstreamCostCents: true,
        },
        _count: true,
      }),
    ]);

    return {
      user,
      ledger: {
        requestCount: totals._count,
        promptTokens: totals._sum.promptTokens ?? 0,
        completionTokens: totals._sum.completionTokens ?? 0,
        totalTokens: totals._sum.totalTokens ?? 0,
        upstreamCostCents: totals._sum.upstreamCostCents ?? 0,
      },
      recent,
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
