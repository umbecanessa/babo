import {
  Controller,
  Get,
  Post,
  Delete,
  Query,
  Param,
  Body,
  UseGuards,
  HttpException,
  HttpStatus,
} from '@nestjs/common';
import { AuthGuard } from '@nestjs/passport';
import { ClawhubService } from './clawhub.service';

@Controller('clawhub')
@UseGuards(AuthGuard('jwt'))
export class ClawhubController {
  constructor(private readonly clawhub: ClawhubService) {}

  @Get('search')
  async search(
    @Query('q') query: string,
    @Query('limit') limit?: string,
  ) {
    if (!query) {
      throw new HttpException('query param "q" is required', HttpStatus.BAD_REQUEST);
    }
    return this.clawhub.search(query, limit ? parseInt(limit, 10) : 20);
  }

  @Get('skill/:slug')
  async skillDetail(@Param('slug') slug: string) {
    const info = await this.clawhub.getSkillInfo(slug);
    if (!info) {
      throw new HttpException('Skill not found', HttpStatus.NOT_FOUND);
    }
    return info;
  }

  @Post('install')
  async install(
    @Body() body: { slug: string; version?: string; agentId: string },
  ) {
    if (!body.slug || !body.agentId) {
      throw new HttpException('slug and agentId are required', HttpStatus.BAD_REQUEST);
    }
    const result = await this.clawhub.installToAgent(
      body.slug,
      body.agentId,
      body.version,
    );
    if (!result.success) {
      throw new HttpException(
        result.error || 'Install failed',
        HttpStatus.INTERNAL_SERVER_ERROR,
      );
    }
    return { status: 'installed', slug: body.slug };
  }

  @Get('installed')
  async installed(@Query('agentId') agentId?: string) {
    return this.clawhub.listInstalled(agentId);
  }

  @Delete('uninstall/:slug')
  async uninstall(
    @Param('slug') slug: string,
    @Query('agentId') agentId?: string,
  ) {
    const ok = await this.clawhub.uninstall(slug, agentId);
    if (!ok) {
      throw new HttpException('Skill not found or already removed', HttpStatus.NOT_FOUND);
    }
    return { status: 'uninstalled', slug };
  }
}
