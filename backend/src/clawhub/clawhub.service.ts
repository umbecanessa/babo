import { Injectable, Logger } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';
import { ChannelsService } from '../channels/channels.service';

const CLAWHUB_API = 'https://clawhub.ai/api/v1';

export interface ClawhubSearchResult {
  slug: string;
  name: string;
  description: string;
  version: string;
  author?: string;
  downloads?: number;
}

export interface ClawhubSkillDetail {
  slug: string;
  name: string;
  description: string;
  version: string;
  author?: string;
  license?: string;
  homepage?: string;
  skillMd: string;
  metadata?: Record<string, any>;
}

@Injectable()
export class ClawhubService {
  private readonly logger = new Logger(ClawhubService.name);

  constructor(
    private prisma: PrismaService,
    private channels: ChannelsService,
  ) {}

  async search(
    query: string,
    limit = 20,
  ): Promise<ClawhubSearchResult[]> {
    try {
      const url = `${CLAWHUB_API}/search?q=${encodeURIComponent(query)}&limit=${limit}`;
      const res = await fetch(url);
      if (!res.ok) {
        this.logger.warn(`ClawHub search failed: ${res.status}`);
        return [];
      }
      const data = await res.json();
      return (data.results ?? data) as ClawhubSearchResult[];
    } catch (err: any) {
      this.logger.warn(`ClawHub search error: ${err.message}`);
      return [];
    }
  }

  async getSkillInfo(slug: string): Promise<ClawhubSkillDetail | null> {
    try {
      const url = `${CLAWHUB_API}/resolve?slug=${encodeURIComponent(slug)}`;
      const res = await fetch(url);
      if (!res.ok) return null;
      return (await res.json()) as ClawhubSkillDetail;
    } catch (err: any) {
      this.logger.warn(`ClawHub getSkillInfo error: ${err.message}`);
      return null;
    }
  }

  async downloadBundle(
    slug: string,
    version?: string,
  ): Promise<Record<string, string> | null> {
    try {
      const tag = version ? encodeURIComponent(version) : 'latest';
      const url = `${CLAWHUB_API}/download?slug=${encodeURIComponent(slug)}&tag=${tag}`;
      const res = await fetch(url);
      if (!res.ok) return null;
      const data = await res.json();
      return data.files as Record<string, string>;
    } catch (err: any) {
      this.logger.warn(`ClawHub download error: ${err.message}`);
      return null;
    }
  }

  async installToAgent(
    slug: string,
    agentId: string,
    version?: string,
  ): Promise<{ success: boolean; error?: string }> {
    const files = await this.downloadBundle(slug, version);
    if (!files) {
      return { success: false, error: 'Failed to download skill bundle from ClawHub' };
    }

    const info = await this.getSkillInfo(slug);

    const pushed = await this.pushSkillInstall(agentId, slug, files);
    if (!pushed) {
      return { success: false, error: 'Agent is offline — skill queued for install' };
    }

    await this.prisma.clawhubSkill.upsert({
      where: {
        slug_agentId: { slug, agentId: agentId },
      },
      create: {
        slug,
        version: version || info?.version || 'latest',
        name: info?.name || slug,
        description: info?.description,
        agentId: agentId,
        metadata: (info?.metadata ?? {}) as any,
        pushed: true,
      },
      update: {
        version: version || info?.version || 'latest',
        pushed: true,
      },
    });

    return { success: true };
  }

  async listInstalled(agentId?: string) {
    const where = agentId ? { agentId } : {};
    return this.prisma.clawhubSkill.findMany({
      where,
      orderBy: { installedAt: 'desc' },
    });
  }

  async uninstall(slug: string, agentId?: string): Promise<boolean> {
    try {
      if (agentId) {
        await this.prisma.clawhubSkill.delete({
          where: { slug_agentId: { slug, agentId } },
        });
      } else {
        await this.prisma.clawhubSkill.deleteMany({
          where: { slug, agentId: null },
        });
      }
      return true;
    } catch {
      return false;
    }
  }

  private async pushSkillInstall(
    agentId: string,
    slug: string,
    files: Record<string, string>,
  ): Promise<boolean> {
    return this.channels.pushSkillInstall(agentId, slug, files);
  }
}
