import { Injectable } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';

@Injectable()
export class SettingsService {
  constructor(private prisma: PrismaService) {}

  async getSettings(userId: string): Promise<Record<string, any>> {
    const row = await this.prisma.userSettings.findUnique({
      where: { userId },
    });
    return (row?.data as Record<string, any>) || {};
  }

  async updateSettings(userId: string, data: Record<string, any>): Promise<Record<string, any>> {
    const result = await this.prisma.userSettings.upsert({
      where: { userId },
      create: { userId, data },
      update: { data },
    });
    return result.data as Record<string, any>;
  }
}
