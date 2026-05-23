import { Controller, Get, UseGuards } from '@nestjs/common';
import { JwtAuthGuard } from '../auth/jwt-auth.guard';
import { AgentsService } from './agents.service';

@Controller('tools')
@UseGuards(JwtAuthGuard)
export class ToolsController {
  constructor(private agents: AgentsService) {}

  @Get('catalog')
  getCatalog() {
    return this.agents.getToolCatalog();
  }

  @Get('catalog/v2')
  getCatalogV2() {
    return this.agents.getToolCatalogV2();
  }

  @Get('bundles')
  getBundles() {
    return this.agents.getToolBundles();
  }
}
