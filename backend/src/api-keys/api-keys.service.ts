import {
  Injectable,
  NotFoundException,
  ForbiddenException,
  BadRequestException,
} from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';
import * as bcrypt from 'bcrypt';
import { v4 as uuidv4 } from 'uuid';

export interface ValidatedApiKey {
  id: string;
  userId: string;
  agentId: string | null;
  rateLimitRpm: number;
  scopes: string[];
}

@Injectable()
export class ApiKeysService {
  private readonly KEY_PREFIX = 'nlsk_';

  constructor(private prisma: PrismaService) {}

  async create(
    userId: string,
    name: string,
    rateLimitRpm = 60,
    agentId?: string,
    scopes: string[] = ['inference', 'gpu'],
  ) {
    if (agentId) {
      await this.assertAgentOwned(userId, agentId);
    }
    // Generate a random API key
    const rawKey = `${this.KEY_PREFIX}${uuidv4().replace(/-/g, '')}`;
    const keyPrefix = rawKey.substring(0, 12) + '...';
    const keyHash = await bcrypt.hash(rawKey, 10);

    const apiKey = await this.prisma.apiKey.create({
      data: {
        userId,
        agentId: agentId ?? null,
        keyHash,
        keyPrefix,
        name,
        rateLimitRpm,
        scopes,
      },
    });

    // Return the full key ONCE (it won't be stored in plain text)
    return {
      id: apiKey.id,
      key: rawKey,
      keyPrefix: apiKey.keyPrefix,
      name: apiKey.name,
      rateLimitRpm: apiKey.rateLimitRpm,
      createdAt: apiKey.createdAt,
    };
  }

  async findAll(userId: string) {
    return this.prisma.apiKey.findMany({
      where: { userId },
      select: {
        id: true,
        agentId: true,
        keyPrefix: true,
        name: true,
        scopes: true,
        rateLimitRpm: true,
        isActive: true,
        totalRequests: true,
        lastUsedAt: true,
        createdAt: true,
      },
      orderBy: { createdAt: 'desc' },
    });
  }

  async revoke(userId: string, id: string) {
    const key = await this.prisma.apiKey.findUnique({ where: { id } });
    if (!key) throw new NotFoundException('API key not found');
    if (key.userId !== userId) throw new ForbiddenException();

    await this.prisma.apiKey.update({
      where: { id },
      data: { isActive: false },
    });

    return { revoked: id };
  }

  async delete(userId: string, id: string) {
    const key = await this.prisma.apiKey.findUnique({ where: { id } });
    if (!key) throw new NotFoundException('API key not found');
    if (key.userId !== userId) throw new ForbiddenException();

    await this.prisma.apiKey.delete({ where: { id } });
    return { deleted: id };
  }

  /** Validate a plaintext API key (inference proxy, automation). */
  async validateKey(
    rawKey: string,
    requiredScopes: string[] = ['inference'],
  ): Promise<ValidatedApiKey | null> {
    if (!rawKey?.startsWith(this.KEY_PREFIX)) {
      return null;
    }

    const keyPrefix = rawKey.substring(0, 12) + '...';
    const candidates = await this.prisma.apiKey.findMany({
      where: { keyPrefix, isActive: true },
    });

    for (const row of candidates) {
      const ok = await bcrypt.compare(rawKey, row.keyHash);
      if (!ok) continue;
      const hasScope = requiredScopes.some((s) => row.scopes.includes(s));
      if (!hasScope) {
        return null;
      }
      return {
        id: row.id,
        userId: row.userId,
        agentId: row.agentId,
        rateLimitRpm: row.rateLimitRpm,
        scopes: row.scopes,
      };
    }
    return null;
  }

  async getRateLimitRpm(apiKeyId: string): Promise<number | null> {
    const row = await this.prisma.apiKey.findUnique({
      where: { id: apiKeyId },
      select: { rateLimitRpm: true, isActive: true },
    });
    if (!row?.isActive) return null;
    return row.rateLimitRpm;
  }

  async touchKey(id: string): Promise<void> {
    await this.prisma.apiKey.update({
      where: { id },
      data: {
        lastUsedAt: new Date(),
        totalRequests: { increment: 1 },
      },
    });
  }

  private async assertAgentOwned(userId: string, agentId: string): Promise<void> {
    const agent = await this.prisma.agent.findFirst({
      where: { id: agentId, userId },
    });
    if (!agent) {
      throw new BadRequestException('Agent not found or not owned by user');
    }
  }
}
