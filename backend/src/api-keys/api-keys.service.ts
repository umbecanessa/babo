import { Injectable, NotFoundException, ForbiddenException } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';
import * as bcrypt from 'bcrypt';
import { v4 as uuidv4 } from 'uuid';

@Injectable()
export class ApiKeysService {
  private readonly KEY_PREFIX = 'nlsk_';

  constructor(private prisma: PrismaService) {}

  async create(userId: string, name: string, rateLimitRpm = 60) {
    // Generate a random API key
    const rawKey = `${this.KEY_PREFIX}${uuidv4().replace(/-/g, '')}`;
    const keyPrefix = rawKey.substring(0, 12) + '...';
    const keyHash = await bcrypt.hash(rawKey, 10);

    const apiKey = await this.prisma.apiKey.create({
      data: {
        userId,
        keyHash,
        keyPrefix,
        name,
        rateLimitRpm,
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
        keyPrefix: true,
        name: true,
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
}
