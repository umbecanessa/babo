import { Injectable, Logger } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';
import { CloudAuthContext } from './cloud-auth.types';
import { EntitlementsService } from './entitlements.service';
import { computeUpstreamCostCents } from './pricing/model-prices';

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
      upstreamCostCents: upstreamCostCentsIn,
    } = params;

    const upstreamCostCents =
      upstreamCostCentsIn ??
      computeUpstreamCostCents(model, usage.promptTokens, usage.completionTokens);

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
          upstreamCostCents,
          requestId,
        },
        update: {
          promptTokens: usage.promptTokens,
          completionTokens: usage.completionTokens,
          totalTokens: usage.totalTokens,
          upstreamCostCents,
          updatedAt: new Date(),
        },
      });

      await this.entitlements.recordUsage(auth.userId, upstreamCostCents, {
        placement,
      });
    } catch (err: any) {
      this.logger.warn(`Usage record failed ${requestId}: ${err.message}`);
    }
  }

  async listForUser(userId: string, limit = 25) {
    const take = Math.min(Math.max(limit, 1), 100);
    const [rows, view] = await Promise.all([
      this.prisma.inferenceUsage.findMany({
        where: { userId },
        orderBy: { createdAt: 'desc' },
        take,
        select: {
          id: true,
          model: true,
          placement: true,
          provider: true,
          route: true,
          workload: true,
          promptTokens: true,
          completionTokens: true,
          totalTokens: true,
          upstreamCostCents: true,
          agentId: true,
          apiKeyId: true,
          requestId: true,
          createdAt: true,
        },
      }),
      this.entitlements.getSubscriptionView(userId),
    ]);

    const ledgerTotal = await this.prisma.inferenceUsage.aggregate({
      where: { userId },
      _sum: { totalTokens: true, upstreamCostCents: true },
      _count: true,
    });

    return {
      subscription: view,
      ledger: {
        requestCount: ledgerTotal._count,
        totalTokens: ledgerTotal._sum.totalTokens ?? 0,
        upstreamCostCents: ledgerTotal._sum.upstreamCostCents ?? 0,
      },
      recent: rows,
    };
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
