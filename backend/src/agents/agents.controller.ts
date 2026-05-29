import { BadRequestException, Controller, Get, Post, Delete, Body, Param, Query, UseGuards, Request } from '@nestjs/common';
import { JwtAuthGuard } from '../auth/jwt-auth.guard';
import { AgentsService } from './agents.service';
import { ChannelsService } from '../channels/channels.service';
import { CreateAgentDto } from './dto';

@Controller('agents')
@UseGuards(JwtAuthGuard)
export class AgentsController {
  constructor(
    private agents: AgentsService,
    private channels: ChannelsService,
  ) {}

  // =================================================================
  // CRUD — collection-level
  // =================================================================

  @Post()
  create(@Request() req: any, @Body() dto: CreateAgentDto) {
    return this.agents.create(req.user.userId, dto.genesisVersion, dto.name, dto.sovereignty, req.user.email, req.user.name);
  }

  @Get()
  findAll(@Request() req: any) {
    return this.agents.findAll(req.user.userId);
  }

  // =================================================================
  // Sync — desktop agent registration
  // NOTE: MUST come before @Get(':id') to avoid being caught by it
  // =================================================================

  @Post('sync')
  async syncAgent(
    @Request() req: any,
    @Body() body: { runtimeAgentId?: string; name?: string; genesisVersion?: string },
  ) {
    const runtimeAgentId = body.runtimeAgentId;
    if (!runtimeAgentId) {
      throw new BadRequestException('runtimeAgentId is required');
    }
    return this.agents.ensureByRuntimeAgentId(
      req.user.userId,
      runtimeAgentId,
      body.name,
      body.genesisVersion,
    );
  }

  // =================================================================
  // Genesis Templates — available education paths
  // NOTE: MUST come before @Get(':id') to avoid being caught by it
  // =================================================================

  @Get('genesis')
  getGenesisTemplates() {
    return this.agents.getGenesisTemplates();
  }

  // =================================================================
  // Relay status — is the desktop runtime online?
  // NOTE: MUST come before @Get(':id') to avoid being caught by it
  // =================================================================

  @Get('relay-status')
  async getAllRelayStatus(@Request() req: any) {
    const agents = await this.agents.findAll(req.user.userId);
    const connectedIds = new Set(this.channels.getConnectedRelayAgents());
    return agents.map((a: any) => ({
      id: a.id,
      runtimeAgentId: a.runtimeAgentId,
      name: a.name,
      online: connectedIds.has(a.runtimeAgentId),
    }));
  }

  @Get(':id/relay-status')
  async getRelayStatus(@Request() req: any, @Param('id') id: string) {
    const runtimeId = await this.agents.getRuntimeAgentId(req.user.userId, id);
    const online = this.channels.hasRelaySocket(runtimeId);
    return { online, runtimeAgentId: runtimeId };
  }

  // =================================================================
  // Agent detail — runtime proxy (user sees only their own agents)
  // NOTE: these MUST come before @Get(':id') to avoid being caught by it
  // =================================================================

  @Get(':id/status')
  async getStatus(@Request() req: any, @Param('id') id: string) {
    const runtimeId = await this.agents.getRuntimeAgentId(req.user.userId, id);
    try {
      return await this.agents.getAgentStatus(req.user.userId, id);
    } catch {
      return {
        agent_id: runtimeId,
        status: this.channels.hasRelaySocket(runtimeId) ? 'online' : 'offline',
        relay: this.channels.hasRelaySocket(runtimeId),
      };
    }
  }

  @Get(':id/chain')
  getChain(@Request() req: any, @Param('id') id: string) {
    return this.agents.getAgentChain(req.user.userId, id);
  }

  @Get(':id/facts')
  getFacts(@Request() req: any, @Param('id') id: string, @Query() query: any) {
    return this.agents.getAgentFacts(req.user.userId, id, query);
  }

  @Get(':id/events')
  getEvents(@Request() req: any, @Param('id') id: string, @Query() query: any) {
    return this.agents.getAgentEvents(req.user.userId, id, query);
  }

  @Get(':id/conversation')
  getConversation(@Request() req: any, @Param('id') id: string) {
    return this.agents.getAgentConversation(req.user.userId, id);
  }

  @Get(':id/config')
  getConfig(@Request() req: any, @Param('id') id: string) {
    return this.agents.getAgentConfig(req.user.userId, id);
  }

  @Get(':id/hormones/history')
  getHormoneHistory(@Request() req: any, @Param('id') id: string) {
    return this.agents.getHormoneHistory(req.user.userId, id);
  }

  @Get(':id/signals/history')
  getSignalHistory(@Request() req: any, @Param('id') id: string) {
    return this.agents.getSignalHistory(req.user.userId, id);
  }

  // =================================================================
  // Tools — batch routes MUST come before parameterized :toolName routes
  // =================================================================

  @Post(':id/tools/batch-enable')
  batchEnableTools(@Request() req: any, @Param('id') id: string, @Body() body: any) {
    return this.agents.batchEnableTools(req.user.userId, id, body);
  }

  @Get(':id/tools/batch/:batchId/status')
  getBatchOnboardingStatus(@Request() req: any, @Param('id') id: string, @Param('batchId') batchId: string) {
    return this.agents.getBatchOnboardingStatus(req.user.userId, id, batchId);
  }

  @Get(':id/tools')
  getTools(@Request() req: any, @Param('id') id: string) {
    return this.agents.getAgentTools(req.user.userId, id);
  }

  @Post(':id/tools/:toolName/enable')
  enableTool(@Request() req: any, @Param('id') id: string, @Param('toolName') toolName: string) {
    return this.agents.enableTool(req.user.userId, id, toolName);
  }

  @Post(':id/tools/:toolName/disable')
  disableTool(@Request() req: any, @Param('id') id: string, @Param('toolName') toolName: string) {
    return this.agents.disableTool(req.user.userId, id, toolName);
  }

  @Get(':id/tools/:toolName/status')
  getToolOnboardingStatus(@Request() req: any, @Param('id') id: string, @Param('toolName') toolName: string) {
    return this.agents.getToolOnboardingStatus(req.user.userId, id, toolName);
  }

  // =================================================================
  // Sessions — thread history proxy
  // =================================================================

  @Get(':id/sessions')
  async getSessions(@Request() req: any, @Param('id') id: string) {
    const runtimeId = await this.agents.getRuntimeAgentId(req.user.userId, id);
    return this.agents.proxyRuntime(runtimeId, `/sessions/${runtimeId}`);
  }

  @Get(':id/sessions/:sessionKey')
  async getSessionHistory(
    @Request() req: any,
    @Param('id') id: string,
    @Param('sessionKey') sessionKey: string,
  ) {
    const runtimeId = await this.agents.getRuntimeAgentId(req.user.userId, id);
    return this.agents.proxyRuntime(runtimeId, `/sessions/${runtimeId}/${encodeURIComponent(sessionKey)}`);
  }

  // =================================================================
  // CRUD — item-level (MUST be after sub-resource routes)
  // =================================================================

  @Get(':id')
  findOne(@Request() req: any, @Param('id') id: string) {
    return this.agents.findOne(req.user.userId, id);
  }

  @Delete(':id')
  remove(@Request() req: any, @Param('id') id: string) {
    return this.agents.remove(req.user.userId, id);
  }

  // =================================================================
  // Actions
  // =================================================================

  @Post(':id/sleep')
  forceSleep(@Request() req: any, @Param('id') id: string) {
    return this.agents.forceSleep(req.user.userId, id);
  }

  // =================================================================
  // Soul Packages — portable agent state
  // =================================================================

  @Get(':id/soul-packages')
  listSoulPackages(@Request() req: any, @Param('id') id: string) {
    return this.agents.listSoulPackages(req.user.userId, id);
  }

  @Post(':id/soul-packages')
  createSoulPackage(
    @Request() req: any,
    @Param('id') id: string,
    @Body() body: { chainHeight?: number; metadata?: any },
  ) {
    return this.agents.createSoulPackage(req.user.userId, id, body);
  }

  @Get(':id/soul-packages/latest')
  getLatestSoulPackage(@Request() req: any, @Param('id') id: string) {
    return this.agents.getLatestSoulPackage(req.user.userId, id);
  }

  // =================================================================
  // Device Leases — exclusive agent access
  // =================================================================

  @Get(':id/lease')
  getActiveLease(@Request() req: any, @Param('id') id: string) {
    return this.agents.getActiveLease(req.user.userId, id);
  }

  @Post(':id/lease/acquire')
  acquireLease(
    @Request() req: any,
    @Param('id') id: string,
    @Body() body: { deviceId: string; deviceName?: string },
  ) {
    return this.agents.acquireLease(req.user.userId, id, body.deviceId, body.deviceName);
  }

  @Post(':id/lease/release')
  releaseLease(
    @Request() req: any,
    @Param('id') id: string,
    @Body() body?: { chainHeight?: number; metadata?: any },
  ) {
    return this.agents.releaseLease(req.user.userId, id, body);
  }

  @Post(':id/lease/force-acquire')
  forceAcquireLease(
    @Request() req: any,
    @Param('id') id: string,
    @Body() body: { deviceId: string; deviceName?: string },
  ) {
    return this.agents.forceAcquireLease(req.user.userId, id, body.deviceId, body.deviceName);
  }

  @Post(':id/lease/heartbeat')
  heartbeatLease(
    @Request() req: any,
    @Param('id') id: string,
    @Body() body: { deviceId: string },
  ) {
    return this.agents.heartbeatLease(req.user.userId, id, body.deviceId);
  }

  // =================================================================
  // Memory Fork — create a new agent from a chain snapshot
  // =================================================================

  @Post(':id/fork')
  forkAgent(
    @Request() req: any,
    @Param('id') id: string,
    @Body() body: { forkHeight: number; newAgentName?: string },
  ) {
    return this.agents.forkAgent(req.user.userId, id, body.forkHeight, body.newAgentName);
  }

}
