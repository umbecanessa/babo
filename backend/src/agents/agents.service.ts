import { Injectable, NotFoundException, ForbiddenException } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';
import { RuntimeService } from '../runtime/runtime.service';

function withRuntimeStatus<T extends { runtimeAgentId: string }>(
  agent: T,
  runtimeStatus?: Record<string, unknown>,
): T & { runtime?: Record<string, unknown> } {
  return runtimeStatus ? { ...agent, runtime: runtimeStatus } : agent;
}

@Injectable()
export class AgentsService {
  constructor(
    private prisma: PrismaService,
    private runtime: RuntimeService,
  ) {}

  // =================================================================
  // Genesis Templates
  // =================================================================

  async getGenesisTemplates() {
    return this.runtime.listGenesis();
  }

  // =================================================================
  // CRUD
  // =================================================================

  async create(userId: string, genesisVersion?: string, name?: string, sovereignty?: string, ownerEmail?: string, ownerName?: string) {
    const runtimeResult = await this.runtime.createAgent({
      genesis_version: genesisVersion || '',
      name: name || '',
      sovereignty: sovereignty || 'local',
      ownerEmail: ownerEmail || '',
      ownerName: ownerName || '',
    });

    // Store ownership in our DB
    const agent = await this.prisma.agent.create({
      data: {
        userId,
        runtimeAgentId: runtimeResult.agent_id,
        name: name || runtimeResult.name || null,
        genesisVersion: genesisVersion || runtimeResult.genesis_version || 'default',
        status: runtimeResult.status || 'alive',
      },
    });

    return withRuntimeStatus(agent, runtimeResult);
  }

  async findAll(userId: string) {
    const agents = await this.prisma.agent.findMany({
      where: { userId },
      orderBy: { createdAt: 'desc' },
    });

    // Enrich with live runtime status
    const enriched = await Promise.all(
      agents.map(async (agent) => {
        try {
          const runtimeStatus = await this.runtime.getAgent(agent.runtimeAgentId);
          return withRuntimeStatus(agent, runtimeStatus);
        } catch {
          return withRuntimeStatus(agent, { status: 'unreachable' });
        }
      }),
    );

    return enriched;
  }

  async findOne(userId: string, id: string) {
    const agent = await this.prisma.agent.findUnique({ where: { id } });
    if (!agent) throw new NotFoundException('Agent not found');
    if (agent.userId !== userId) throw new ForbiddenException();

    try {
      const runtimeStatus = await this.runtime.getAgent(agent.runtimeAgentId);
      return withRuntimeStatus(agent, runtimeStatus);
    } catch {
      return withRuntimeStatus(agent, { status: 'unreachable' });
    }
  }

  async remove(userId: string, id: string) {
    const agent = await this.prisma.agent.findUnique({ where: { id } });
    if (!agent) throw new NotFoundException('Agent not found');
    if (agent.userId !== userId) throw new ForbiddenException();

    try {
      await this.runtime.deleteAgent(agent.runtimeAgentId);
    } catch {
      // Runtime may already have removed the agent
    }

    await this.prisma.agent.delete({ where: { id } });
    return { deleted: id };
  }

  async findByRuntimeAgentId(runtimeAgentId: string): Promise<string | null> {
    const agent = await this.prisma.agent.findFirst({
      where: { runtimeAgentId },
      select: { id: true },
    });
    return agent?.id || null;
  }

  async syncOwnerEmail(runtimeAgentId: string, email: string): Promise<void> {
    await this.runtime.patchAgent(runtimeAgentId, 'owner-email', { ownerEmail: email });
  }

  async syncOwnerIdentity(runtimeAgentId: string, email: string, displayName?: string): Promise<void> {
    const body: Record<string, string> = { ownerEmail: email };
    if (displayName) body['ownerName'] = displayName;
    await this.runtime.patchAgent(runtimeAgentId, 'owner-email', body);
  }

  async findOneByRuntimeAgentId(userId: string, runtimeAgentId: string) {
    const agent = await this.prisma.agent.findFirst({ where: { runtimeAgentId } });
    if (!agent) throw new NotFoundException('Agent not found');
    if (agent.userId !== userId) throw new ForbiddenException();

    try {
      const runtimeStatus = await this.runtime.getAgent(agent.runtimeAgentId);
      return withRuntimeStatus(agent, runtimeStatus);
    } catch {
      return withRuntimeStatus(agent, { status: 'unreachable' });
    }
  }

  /**
   * Look up an agent by Python runtime agent ID without ownership checks.
   */
  async findOneByRuntimeAgentIdUnsafe(runtimeAgentId: string) {
    return this.prisma.agent.findFirst({ where: { runtimeAgentId } });
  }

  /**
   * Ensure a NestJS DB record exists for an agent created on the desktop runtime.
   */
  async ensureByRuntimeAgentId(
    userId: string,
    runtimeAgentId: string,
    providedName?: string,
    providedGenesis?: string,
  ) {
    const existing = await this.prisma.agent.findFirst({ where: { runtimeAgentId } });
    if (existing) {
      if (existing.userId !== userId) throw new ForbiddenException();
      // Update name/genesis if they were missing and are now provided
      if (providedName && !existing.name) {
        await this.prisma.agent.update({
          where: { id: existing.id },
          data: { name: providedName },
        });
      }
      return existing;
    }

    let name: string | null = providedName || null;
    let genesisVersion = providedGenesis || 'default';
    let status = 'alive';

    if (!name) {
      try {
        const info = await this.runtime.getAgent(runtimeAgentId);
        name = info.name || null;
        genesisVersion = info.genesis_version || genesisVersion;
        status = info.status || 'alive';
      } catch {
        // Desktop-created agents may not be reachable via direct HTTP
      }
    }

    return this.prisma.agent.create({
      data: {
        userId,
        runtimeAgentId,
        name,
        genesisVersion,
        status,
      },
    });
  }

  async getRuntimeAgentId(userId: string, agentId: string): Promise<string> {
    const agent = await this.resolveOwnedAgent(userId, agentId);
    return agent.runtimeAgentId;
  }

  /**
   * Desktop agents are addressed by runtime UUID in URLs; Nest DB rows use a separate id.
   * Accept either when resolving ownership.
   */
  private async resolveOwnedAgent(userId: string, agentOrRuntimeId: string) {
    let agent = await this.prisma.agent.findUnique({ where: { id: agentOrRuntimeId } });
    if (!agent) {
      agent = await this.prisma.agent.findFirst({
        where: { runtimeAgentId: agentOrRuntimeId, userId },
      });
    }
    if (!agent) throw new NotFoundException('Agent not found');
    if (agent.userId !== userId) throw new ForbiddenException();
    return agent;
  }

  /**
   * Update the agent's display name in the local PostgreSQL database.
   * Called when the agent accepts a name during conversation.
   */
  async updateName(userId: string, agentId: string, name: string) {
    const agent = await this.prisma.agent.findUnique({ where: { id: agentId } });
    if (!agent) throw new NotFoundException('Agent not found');
    if (agent.userId !== userId) throw new ForbiddenException();

    return this.prisma.agent.update({
      where: { id: agentId },
      data: { name },
    });
  }

  // =================================================================
  // Runtime proxy — agent detail data
  // =================================================================

  private async fetchRuntime(path: string, method: string = 'GET', body?: any): Promise<any> {
    try {
      if (method === 'POST') {
        return await this.runtime.proxyPost(path, body);
      }
      return await this.runtime.proxyGet(path);
    } catch (err: any) {
      const msg = typeof err.message === 'string' ? err.message : JSON.stringify(err.message);
      throw new NotFoundException(msg || 'Runtime error');
    }
  }

  private cleanParams(query: Record<string, string>): URLSearchParams {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== null && value !== '') {
        params.set(key, String(value));
      }
    }
    return params;
  }

  async getAgentStatus(userId: string, agentId: string) {
    const runtimeId = await this.getRuntimeAgentId(userId, agentId);
    return this.runtime.getAgent(runtimeId);
  }

  async getAgentChain(userId: string, agentId: string) {
    const runtimeId = await this.getRuntimeAgentId(userId, agentId);
    return this.fetchRuntime(`/admin/agents/${runtimeId}/chain`);
  }

  async getAgentFacts(userId: string, agentId: string, query: Record<string, string>) {
    const runtimeId = await this.getRuntimeAgentId(userId, agentId);
    const qs = this.cleanParams(query).toString();
    return this.fetchRuntime(`/admin/agents/${runtimeId}/facts${qs ? '?' + qs : ''}`);
  }

  async getAgentEvents(userId: string, agentId: string, query: Record<string, string>) {
    const runtimeId = await this.getRuntimeAgentId(userId, agentId);
    const qs = this.cleanParams(query).toString();
    return this.fetchRuntime(`/admin/agents/${runtimeId}/events${qs ? '?' + qs : ''}`);
  }

  async getAgentConversation(userId: string, agentId: string) {
    const runtimeId = await this.getRuntimeAgentId(userId, agentId);
    return this.fetchRuntime(`/admin/agents/${runtimeId}/conversation`);
  }

  async getAgentConfig(userId: string, agentId: string) {
    const runtimeId = await this.getRuntimeAgentId(userId, agentId);
    return this.fetchRuntime(`/admin/agents/${runtimeId}/config`);
  }

  async getHormoneHistory(userId: string, agentId: string) {
    const runtimeId = await this.getRuntimeAgentId(userId, agentId);
    return this.fetchRuntime(`/admin/agents/${runtimeId}/hormones/history`);
  }

  async getSignalHistory(userId: string, agentId: string) {
    const runtimeId = await this.getRuntimeAgentId(userId, agentId);
    return this.fetchRuntime(`/admin/agents/${runtimeId}/signals/history`);
  }

  async forceSleep(userId: string, agentId: string) {
    const runtimeId = await this.getRuntimeAgentId(userId, agentId);
    return this.fetchRuntime(`/admin/agents/${runtimeId}/sleep`, 'POST');
  }

  // =================================================================
  // Tools
  // =================================================================

  async getToolCatalog() {
    return this.fetchRuntime('/admin/tools/catalog');
  }

  async getToolCatalogV2() {
    return this.fetchRuntime('/admin/tools/catalog/v2');
  }

  async getAgentTools(userId: string, agentId: string) {
    const runtimeId = await this.getRuntimeAgentId(userId, agentId);
    return this.fetchRuntime(`/admin/agents/${runtimeId}/tools`);
  }

  async enableTool(userId: string, agentId: string, toolName: string) {
    const runtimeId = await this.getRuntimeAgentId(userId, agentId);
    return this.fetchRuntime(`/admin/agents/${runtimeId}/tools/${toolName}/enable`, 'POST');
  }

  async disableTool(userId: string, agentId: string, toolName: string) {
    const runtimeId = await this.getRuntimeAgentId(userId, agentId);
    return this.fetchRuntime(`/admin/agents/${runtimeId}/tools/${toolName}/disable`, 'POST');
  }

  async getToolOnboardingStatus(userId: string, agentId: string, toolName: string) {
    const runtimeId = await this.getRuntimeAgentId(userId, agentId);
    return this.fetchRuntime(`/admin/agents/${runtimeId}/tools/${toolName}/status`);
  }

  async getToolBundles() {
    return this.fetchRuntime('/admin/tools/bundles');
  }

  async batchEnableTools(userId: string, agentId: string, body: any) {
    const runtimeId = await this.getRuntimeAgentId(userId, agentId);
    return this.fetchRuntime(`/admin/agents/${runtimeId}/tools/batch-enable`, 'POST', body);
  }

  async getBatchOnboardingStatus(userId: string, agentId: string, batchId: string) {
    const runtimeId = await this.getRuntimeAgentId(userId, agentId);
    return this.fetchRuntime(`/admin/agents/${runtimeId}/tools/batch/${batchId}/status`);
  }

  // =================================================================
  // Soul Packages — portable agent state
  // =================================================================

  async listSoulPackages(userId: string, agentId: string) {
    await this.getRuntimeAgentId(userId, agentId); // ownership check
    return this.prisma.soulPackage.findMany({
      where: { agentId },
      orderBy: { createdAt: 'desc' },
      take: 20,
    });
  }

  async createSoulPackage(
    userId: string,
    agentId: string,
    body: { chainHeight?: number; metadata?: any },
  ) {
    const runtimeId = await this.getRuntimeAgentId(userId, agentId);

    let soulData: any = {};
    try {
      soulData = await this.fetchRuntime(
        `/admin/agents/${runtimeId}/soul/export?include_sessions=false`,
      );
    } catch {
      // If runtime is unreachable, create a metadata-only record
    }

    return this.prisma.soulPackage.create({
      data: {
        agentId,
        chainHeight: body.chainHeight || soulData.chain_height || 0,
        metadata: body.metadata || soulData || {},
        storageUrl: soulData.download_url || null,
        sizeBytes: soulData.size_bytes || 0,
        checksum: soulData.checksum || null,
      },
    });
  }

  async getLatestSoulPackage(userId: string, agentId: string) {
    await this.getRuntimeAgentId(userId, agentId);
    const pkg = await this.prisma.soulPackage.findFirst({
      where: { agentId },
      orderBy: { createdAt: 'desc' },
    });
    if (!pkg) throw new NotFoundException('No soul packages found');
    return pkg;
  }

  /** Generic runtime proxy (for browser clients going through NestJS). */
  async proxyRuntime(
    runtimeId: string,
    path: string,
    method: string = 'GET',
    body?: any,
  ): Promise<any> {
    return this.fetchRuntime(path, method, body);
  }

  /** Internal (service-to-service) soul-package creation — no user ownership check. */
  async createSoulPackageInternal(
    agentId: string,
    body: { chainHeight?: number; metadata?: any },
  ) {
    return this.prisma.soulPackage.create({
      data: {
        agentId,
        chainHeight: body.chainHeight || 0,
        metadata: body.metadata || {},
      },
    });
  }

  // =================================================================
  // Device Leases — exclusive agent access
  // =================================================================

  async getActiveLease(userId: string, agentId: string) {
    await this.getRuntimeAgentId(userId, agentId);
    const lease = await this.prisma.deviceLease.findFirst({
      where: { agentId, isActive: true },
    });
    return { lease, hasActiveLease: !!lease };
  }

  async acquireLease(
    userId: string,
    agentId: string,
    deviceId: string,
    deviceName?: string,
  ) {
    await this.getRuntimeAgentId(userId, agentId);

    // Check for existing active lease
    const existing = await this.prisma.deviceLease.findFirst({
      where: { agentId, isActive: true },
    });

    if (existing) {
      // If same device, just refresh heartbeat
      if (existing.deviceId === deviceId) {
        return this.prisma.deviceLease.update({
          where: { id: existing.id },
          data: { lastHeartbeat: new Date() },
        });
      }

      // Check if existing lease is stale (no heartbeat for 5 minutes)
      const staleThreshold = new Date(Date.now() - 5 * 60 * 1000);
      if (existing.lastHeartbeat < staleThreshold) {
        // Release stale lease
        await this.prisma.deviceLease.update({
          where: { id: existing.id },
          data: { isActive: false, releasedAt: new Date() },
        });
      } else {
        throw new ForbiddenException(
          `Agent is currently leased to device "${existing.deviceName || existing.deviceId}". ` +
          `Release it first or wait for the lease to expire.`,
        );
      }
    }

    return this.prisma.deviceLease.create({
      data: {
        agentId,
        deviceId,
        deviceName: deviceName || null,
      },
    });
  }

  async releaseLease(
    userId: string,
    agentId: string,
    soulPackageMeta?: { chainHeight?: number; metadata?: any },
  ) {
    await this.getRuntimeAgentId(userId, agentId);

    const lease = await this.prisma.deviceLease.findFirst({
      where: { agentId, isActive: true },
    });

    if (!lease) throw new NotFoundException('No active lease found');

    await this.prisma.deviceLease.update({
      where: { id: lease.id },
      data: { isActive: false, releasedAt: new Date() },
    });

    // Store soul package snapshot if provided
    if (soulPackageMeta) {
      await this.prisma.soulPackage.create({
        data: {
          agentId,
          chainHeight: soulPackageMeta.chainHeight || 0,
          metadata: soulPackageMeta.metadata || {},
        },
      });
    }

    return { released: true };
  }

  async forceAcquireLease(
    userId: string,
    agentId: string,
    deviceId: string,
    deviceName?: string,
  ) {
    await this.getRuntimeAgentId(userId, agentId);

    // Force-release any existing lease
    const existing = await this.prisma.deviceLease.findFirst({
      where: { agentId, isActive: true },
    });

    if (existing) {
      await this.prisma.deviceLease.update({
        where: { id: existing.id },
        data: { isActive: false, releasedAt: new Date() },
      });
    }

    return this.prisma.deviceLease.create({
      data: {
        agentId,
        deviceId,
        deviceName: deviceName || null,
      },
    });
  }

  async heartbeatLease(userId: string, agentId: string, deviceId: string) {
    await this.getRuntimeAgentId(userId, agentId);

    const lease = await this.prisma.deviceLease.findFirst({
      where: { agentId, isActive: true, deviceId },
    });

    if (!lease) throw new NotFoundException('No active lease for this device');

    return this.prisma.deviceLease.update({
      where: { id: lease.id },
      data: { lastHeartbeat: new Date() },
    });
  }

  // =================================================================
  // Memory Fork
  // =================================================================

  async forkAgent(
    userId: string,
    agentId: string,
    forkHeight: number,
    newAgentName?: string,
  ) {
    const runtimeId = await this.getRuntimeAgentId(userId, agentId);

    const forkResult = await this.runtime.proxyPost(
      `/admin/agents/${runtimeId}/soul/fork`,
      {
        fork_height: forkHeight,
        new_agent_name: newAgentName || '',
      },
    );

    const newRuntimeId = forkResult.new_agent_id;
    if (newRuntimeId) {
      await this.prisma.agent.create({
        data: {
          userId,
          runtimeAgentId: newRuntimeId,
          name: newAgentName || `Fork of ${agentId}`,
          genesisVersion: 'fork',
          status: 'alive',
        },
      });
    }

    return forkResult;
  }
}
