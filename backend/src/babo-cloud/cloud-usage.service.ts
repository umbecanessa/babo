import { Injectable, Logger } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';
import { CloudAuthContext } from './cloud-auth.types';
import { EntitlementsService } from './entitlements.service';

export interface UsageSnapshot {
  promptTokens: number;
  completionTokens: number;
  totalTokens: number;
}

@Injectable()
export class CloudUsageService {
  private readonly logger = new Logger(CloudUsageService.name);

  constructor(
    private prisma: PrismaService,
    private entitlements: EntitlementsService,
  ) {}

  async record(params: {
    auth: CloudAuthContext;
    requestId: string;
    workload: string;
    placement: string;
    model: string;
    route: string;
    provider?: string;
    usage: UsageSnapshot;
    upstreamCostCents?: number;
  }): Promise<void> {
    const {
      auth,
      requestId,
      workload,
      placement,
      model,
      route,
      provider,
      usage,
      upstreamCostCents,
    } = params;

    try {
      await this.prisma.inferenceUsage.upsert({
        where: { id: requestId },
        create: {
          id: requestId,
          userId: auth.userId,
          apiKeyId: auth.apiKeyId ?? null,
          agentId: auth.agentId ?? null,
          workload,
          placement,
          provider: provider ?? null,
          model,
          route,
          promptTokens: usage.promptTokens,
          completionTokens: usage.completionTokens,
          totalTokens: usage.totalTokens,
          upstreamCostCents: upstreamCostCents ?? null,
          requestId,
        },
        update: {
          promptTokens: usage.promptTokens,
          completionTokens: usage.completionTokens,
          totalTokens: usage.totalTokens,
          upstreamCostCents: upstreamCostCents ?? undefined,
          updatedAt: new Date(),
        },
      });

      if (usage.totalTokens > 0) {
        await this.entitlements.addTokenUsage(auth.userId, usage.totalTokens);
      }
    } catch (err: any) {
      this.logger.warn(`Usage record failed ${requestId}: ${err.message}`);
    }
  }

  normalizeUsage(usage: any): UsageSnapshot {
    const promptTokens = Number(usage.prompt_tokens ?? usage.promptTokens ?? 0);
    const completionTokens = Number(
      usage.completion_tokens ?? usage.completionTokens ?? 0,
    );
    const totalTokens = Number(
      usage.total_tokens ??
        usage.totalTokens ??
        promptTokens + completionTokens,
    );
    return { promptTokens, completionTokens, totalTokens };
  }
}
