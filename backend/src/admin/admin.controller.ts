import {
  Controller,
  Get,
  Post,
  Delete,
  Patch,
  Param,
  Query,
  Body,
  Req,
  UseGuards,
} from '@nestjs/common';
import { AdminAuthGuard } from './admin-auth.guard';
import { AdminService } from './admin.service';
import { UpdateRoleDto, AgentEventsQueryDto, AgentFactsQueryDto } from './dto';

@Controller('admin')
@UseGuards(AdminAuthGuard)
export class AdminController {
  constructor(private admin: AdminService) {}

  // ===================================================================
  // Stats / Overview
  // ===================================================================

  @Get('stats')
  getStats() {
    return this.admin.getStats();
  }

  @Get('dashboard')
  getDashboard() {
    return this.admin.getDashboard();
  }

  @Get('platform')
  getPlatform() {
    return this.admin.getPlatformInfo();
  }

  @Get('billing/subscriptions')
  listBillingSubscriptions() {
    return this.admin.listBillingSubscriptions();
  }

  // ===================================================================
  // Users
  // ===================================================================

  @Get('users')
  listUsers() {
    return this.admin.listUsers();
  }

  @Get('users/:id/usage')
  getUserUsage(@Param('id') id: string, @Query('limit') limit?: string) {
    const n = limit ? parseInt(limit, 10) : 50;
    return this.admin.getUserUsage(id, Number.isFinite(n) ? n : 50);
  }

  @Get('users/:id')
  getUser(@Param('id') id: string) {
    return this.admin.getUser(id);
  }

  @Patch('users/:id/role')
  updateUserRole(@Param('id') id: string, @Body() dto: UpdateRoleDto) {
    return this.admin.updateUserRole(id, dto.role);
  }

  @Post('users/:id/grant-lifetime')
  grantLifetime(
    @Param('id') id: string,
    @Req() req: any,
    @Body() body: { grantNote?: string },
  ) {
    return this.admin.grantLifetimeComp(id, req.user.userId, body.grantNote);
  }

  @Post('users/:id/revoke-lifetime')
  revokeLifetime(@Param('id') id: string) {
    return this.admin.revokeLifetimeComp(id);
  }

  /** Dev / pre-Stripe: activate cloud_basic with $5 included usage. */
  @Post('users/:id/activate-cloud-basic')
  activateCloudBasic(@Param('id') id: string) {
    return this.admin.activateCloudBasicDev(id);
  }

  @Delete('users/:id')
  deleteUser(@Param('id') id: string) {
    return this.admin.deleteUser(id);
  }

  // ===================================================================
  // Agents (DB-level)
  // ===================================================================

  @Get('agents')
  listAllAgents() {
    return this.admin.listAllAgents();
  }

  @Get('agents/db/:id/inspect')
  inspectAgent(@Param('id') id: string) {
    return this.admin.getAgentInspect(id);
  }

  @Get('agents/db/:id')
  getAgentDetail(@Param('id') id: string) {
    return this.admin.getAgentDetail(id);
  }

  @Delete('agents/db/:id')
  deleteAgent(@Param('id') id: string) {
    return this.admin.deleteAgent(id);
  }

  // ===================================================================
  // Agent runtime proxy (by runtimeAgentId)
  // ===================================================================

  @Get('agents/:runtimeAgentId/status')
  getAgentStatus(@Param('runtimeAgentId') runtimeAgentId: string) {
    return this.admin.proxyGetAgentStatus(runtimeAgentId);
  }

  @Get('agents/:runtimeAgentId/chain')
  getAgentChain(@Param('runtimeAgentId') runtimeAgentId: string) {
    return this.admin.proxyGetAgentChain(runtimeAgentId);
  }

  @Get('agents/:runtimeAgentId/facts')
  getAgentFacts(@Param('runtimeAgentId') runtimeAgentId: string, @Query() query: AgentFactsQueryDto) {
    return this.admin.proxyGetAgentFacts(runtimeAgentId, query as any);
  }

  @Get('agents/:runtimeAgentId/events')
  getAgentEvents(@Param('runtimeAgentId') runtimeAgentId: string, @Query() query: AgentEventsQueryDto) {
    return this.admin.proxyGetAgentEvents(runtimeAgentId, query as any);
  }

  @Get('agents/:runtimeAgentId/conversation')
  getAgentConversation(@Param('runtimeAgentId') runtimeAgentId: string) {
    return this.admin.proxyGetAgentConversation(runtimeAgentId);
  }

  @Get('agents/:runtimeAgentId/config')
  getAgentConfig(@Param('runtimeAgentId') runtimeAgentId: string) {
    return this.admin.proxyGetAgentConfig(runtimeAgentId);
  }

  @Get('agents/:runtimeAgentId/memory-tiers')
  getAgentMemoryTiers(@Param('runtimeAgentId') runtimeAgentId: string) {
    return this.admin.proxyGetAgentMemoryTiers(runtimeAgentId);
  }

  @Get('agents/:runtimeAgentId/hormones/history')
  getHormoneHistory(@Param('runtimeAgentId') runtimeAgentId: string) {
    return this.admin.proxyGetHormoneHistory(runtimeAgentId);
  }

  @Get('agents/:runtimeAgentId/signals/history')
  getSignalHistory(@Param('runtimeAgentId') runtimeAgentId: string) {
    return this.admin.proxyGetSignalHistory(runtimeAgentId);
  }

  @Post('agents/:runtimeAgentId/evict')
  evictAgent(@Param('runtimeAgentId') runtimeAgentId: string) {
    return this.admin.proxyEvictAgent(runtimeAgentId);
  }

  @Post('agents/:runtimeAgentId/sleep')
  forceSleep(@Param('runtimeAgentId') runtimeAgentId: string) {
    return this.admin.proxyForceSleeep(runtimeAgentId);
  }

  // ===================================================================
  // System (runtime proxy)
  // ===================================================================

  @Get('system/health')
  getSystemHealth() {
    return this.admin.proxyGetSystemHealth();
  }

  @Get('system/adapters')
  getAdapterRegistry() {
    return this.admin.proxyGetAdapterRegistry();
  }

  // ===================================================================
  // Analytics (runtime proxy)
  // ===================================================================

  @Get('analytics/overview')
  getAnalyticsOverview() {
    return this.admin.proxyGetAnalyticsOverview();
  }

  @Get('analytics/compare')
  compareAgents(@Query('ids') ids: string) {
    return this.admin.proxyCompareAgents(ids);
  }

  // ===================================================================
  // Inference usage (Postgres ledger)
  // ===================================================================

  @Get('usage')
  getUsageOverview(@Query('limit') limit?: string) {
    const n = limit ? parseInt(limit, 10) : 50;
    return this.admin.getUsageOverview(Number.isFinite(n) ? n : 50);
  }
}
