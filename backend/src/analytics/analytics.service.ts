import { Injectable, NotFoundException } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { Prisma } from '@prisma/client';
import { randomBytes } from 'crypto';
import { PrismaService } from '../prisma/prisma.service';
import { AnalyticsEventDto } from './dto/track-events.dto';

const BLOCKED_PROPERTY_KEYS = new Set([
  'email',
  'password',
  'token',
  'accessToken',
  'refreshToken',
  'apiKey',
  'inferenceApiKey',
  'gpuWorkerSecret',
  'nestjsUrl',
  'url',
]);

const MAX_PROPERTY_KEYS = 32;
const MAX_STRING_LENGTH = 256;

const SETUP_STEP_ORDER = [
  'welcome',
  'prepare',
  'device',
  'thinking',
  'extras',
  'placement',
  'signin',
  'billing',
  'ready',
  'name',
];

const RETENTION_DAYS = 90;
const PURGE_INTERVAL_MS = 6 * 60 * 60 * 1000;

export interface FunnelEventRow {
  name: string;
  source: string;
  count: number;
  uniqueVisitors: number;
}

export interface FunnelOverview {
  enabled: boolean;
  periodDays: number;
  message?: string;
  web: {
    pageViews: number;
    uniqueVisitors: number;
    ctaClicks: number;
    outboundClicks: number;
    events: FunnelEventRow[];
    ctaByLocation: { location: string; count: number; uniqueVisitors: number }[];
    audiences: { audience: string; uniqueVisitors: number }[];
    campaigns: {
      campaign: string;
      source: string;
      pageViews: number;
      uniqueVisitors: number;
    }[];
  } | null;
  app: {
    setupStarted: number;
    setupCompleted: number;
    setupAbandoned: number;
    billingActivated: number;
    events: FunnelEventRow[];
    steps: {
      step: string;
      views: number;
      uniqueInstalls: number;
      dropOffFromPrevious: number | null;
    }[];
    completionRate: number | null;
  } | null;
  attribution: {
    handoffsCreated: number;
    handoffsClaimed: number;
    claimToSetupStarted: number;
    claimToCompleted: number;
    byCampaign: {
      campaign: string;
      handoffs: number;
      claimed: number;
      completed: number;
    }[];
  } | null;
}

@Injectable()
export class AnalyticsService {
  private lastPurgeAt = 0;

  constructor(
    private config: ConfigService,
    private prisma: PrismaService,
  ) {}

  isEnabled(): boolean {
    const raw = (this.config.get<string>('BABO_ANALYTICS_ENABLED') ?? '').trim().toLowerCase();
    return raw === 'true' || raw === '1' || raw === 'yes';
  }

  getPublicConfig() {
    return { enabled: this.isEnabled() };
  }

  async ingestEvents(
    events: AnalyticsEventDto[],
    opts: { userId?: string | null; source: string },
  ): Promise<{ accepted: number }> {
    if (!this.isEnabled() || events.length === 0) {
      return { accepted: 0 };
    }

    const rows = events.map((event) => ({
      name: event.name.trim().slice(0, 64),
      source: opts.source,
      installId: event.installId?.trim().slice(0, 64) || null,
      userId: opts.userId ?? null,
      platform: event.platform?.trim().slice(0, 16) || null,
      appVersion: event.appVersion?.trim().slice(0, 64) || null,
      properties: this.sanitizeProperties(event.properties) as Prisma.InputJsonValue,
      occurredAt: event.occurredAt ? new Date(event.occurredAt) : new Date(),
    }));

    await this.prisma.productAnalyticsEvent.createMany({ data: rows });
    void this.maybePurgeOldEvents();
    return { accepted: rows.length };
  }

  async getFunnelOverview(days = 30): Promise<FunnelOverview> {
    const periodDays = Math.min(Math.max(days, 1), 365);
    if (!this.isEnabled()) {
      return {
        enabled: false,
        periodDays,
        message:
          'Product analytics is disabled. Set BABO_ANALYTICS_ENABLED=true on the server to collect funnel data.',
        web: null,
        app: null,
        attribution: null,
      };
    }

    try {
      return await this.buildFunnelOverview(periodDays);
    } catch (err: any) {
      const msg = String(err?.message ?? err);
      if (
        msg.includes('product_analytics_events') ||
        msg.includes('analytics_attribution_handoffs') ||
        msg.includes('does not exist')
      ) {
        return {
          enabled: true,
          periodDays,
          message:
            'Analytics is enabled but the database table is missing. Run: npx prisma migrate deploy',
          web: null,
          app: null,
          attribution: null,
        };
      }
      throw err;
    }
  }

  async createHandoff(
    visitorId?: string,
    properties?: Record<string, unknown>,
  ): Promise<{ ref: string | null; clipPayload: string | null }> {
    if (!this.isEnabled()) {
      return { ref: null, clipPayload: null };
    }

    let ref = '';
    for (let attempt = 0; attempt < 8; attempt++) {
      ref = randomBytes(6).toString('hex');
      const exists = await this.prisma.analyticsAttributionHandoff.findUnique({
        where: { ref },
      });
      if (!exists) break;
    }

    const expiresAt = new Date(Date.now() + 30 * 24 * 60 * 60 * 1000);
    await this.prisma.analyticsAttributionHandoff.create({
      data: {
        ref,
        visitorId: visitorId?.trim().slice(0, 64) || null,
        properties: this.sanitizeProperties(properties) as Prisma.InputJsonValue,
        expiresAt,
      },
    });

    return { ref, clipPayload: `babo:ref:${ref}` };
  }

  async claimHandoff(
    ref: string,
    installId: string,
  ): Promise<{ ok: boolean; ref: string; properties: Record<string, unknown> }> {
    if (!this.isEnabled()) {
      return { ok: false, ref: '', properties: {} };
    }

    const normalizedRef = ref.trim().toLowerCase().slice(0, 32);
    const normalizedInstall = installId.trim().slice(0, 64);
    const row = await this.prisma.analyticsAttributionHandoff.findUnique({
      where: { ref: normalizedRef },
    });

    if (!row || row.expiresAt < new Date()) {
      throw new NotFoundException('Attribution reference expired or not found');
    }

    if (row.claimedInstallId && row.claimedInstallId !== normalizedInstall) {
      throw new NotFoundException('Attribution reference already claimed');
    }

    if (!row.claimedAt) {
      await this.prisma.analyticsAttributionHandoff.update({
        where: { ref: normalizedRef },
        data: {
          claimedAt: new Date(),
          claimedInstallId: normalizedInstall,
        },
      });
    }

    const props = row.properties;
    return {
      ok: true,
      ref: normalizedRef,
      properties:
        props && typeof props === 'object' && !Array.isArray(props)
          ? (props as Record<string, unknown>)
          : {},
    };
  }

  private async maybePurgeOldEvents(): Promise<void> {
    const now = Date.now();
    if (now - this.lastPurgeAt < PURGE_INTERVAL_MS) return;
    this.lastPurgeAt = now;
    const cutoff = new Date(now - RETENTION_DAYS * 24 * 60 * 60 * 1000);
    await this.prisma.productAnalyticsEvent.deleteMany({
      where: { occurredAt: { lt: cutoff } },
    });
    await this.prisma.analyticsAttributionHandoff.deleteMany({
      where: { expiresAt: { lt: new Date() } },
    });
  }

  private async buildFunnelOverview(periodDays: number): Promise<FunnelOverview> {
    const since = new Date(Date.now() - periodDays * 24 * 60 * 60 * 1000);

    const eventCounts = await this.prisma.$queryRaw<
      Array<{ name: string; source: string; count: bigint; unique_visitors: bigint }>
    >`
      SELECT
        name,
        source,
        COUNT(*)::bigint AS count,
        COUNT(DISTINCT COALESCE(install_id, id))::bigint AS unique_visitors
      FROM product_analytics_events
      WHERE occurred_at >= ${since}
      GROUP BY name, source
      ORDER BY name ASC
    `;

    const events: FunnelEventRow[] = eventCounts.map((row) => ({
      name: row.name,
      source: row.source,
      count: Number(row.count),
      uniqueVisitors: Number(row.unique_visitors),
    }));

    const countFor = (name: string, source: string) =>
      events.find((e) => e.name === name && e.source === source)?.uniqueVisitors ?? 0;

    const ctaRows = await this.prisma.$queryRaw<
      Array<{ location: string; count: bigint; unique_visitors: bigint }>
    >`
      SELECT
        COALESCE(properties->>'location', '(unknown)') AS location,
        COUNT(*)::bigint AS count,
        COUNT(DISTINCT COALESCE(install_id, id))::bigint AS unique_visitors
      FROM product_analytics_events
      WHERE occurred_at >= ${since}
        AND source = 'web'
        AND name IN ('cta_click', 'outbound_click')
      GROUP BY 1
      ORDER BY count DESC
    `;

    const audienceRows = await this.prisma.$queryRaw<
      Array<{ audience: string; unique_visitors: bigint }>
    >`
      SELECT
        COALESCE(properties->>'audience', '(unknown)') AS audience,
        COUNT(DISTINCT COALESCE(install_id, id))::bigint AS unique_visitors
      FROM product_analytics_events
      WHERE occurred_at >= ${since}
        AND source = 'web'
        AND name = 'landing_page_view'
      GROUP BY 1
      ORDER BY unique_visitors DESC
    `;

    const campaignRows = await this.prisma.$queryRaw<
      Array<{
        campaign: string;
        source: string;
        page_views: bigint;
        unique_visitors: bigint;
      }>
    >`
      SELECT
        COALESCE(properties->>'utm_campaign', '(none)') AS campaign,
        COALESCE(properties->>'utm_source', '(none)') AS source,
        COUNT(*)::bigint AS page_views,
        COUNT(DISTINCT install_id)::bigint AS unique_visitors
      FROM product_analytics_events
      WHERE occurred_at >= ${since}
        AND source = 'web'
        AND name = 'landing_page_view'
      GROUP BY 1, 2
      ORDER BY unique_visitors DESC
      LIMIT 25
    `;

    const stepProgress = await this.prisma.$queryRaw<
      Array<{ install_id: string; max_step: number | null }>
    >`
      SELECT
        install_id,
        MAX(
          CASE
            WHEN name = 'setup_completed' THEN 9
            WHEN name = 'setup_abandoned' THEN COALESCE(NULLIF(properties->>'highest_step_reached', '')::int, 0)
            WHEN name = 'setup_step_viewed' THEN COALESCE(NULLIF(properties->>'step', '')::int, 0)
            ELSE NULL
          END
        )::int AS max_step
      FROM product_analytics_events
      WHERE occurred_at >= ${since}
        AND source = 'app'
        AND install_id IS NOT NULL
        AND name IN ('setup_step_viewed', 'setup_abandoned', 'setup_completed')
      GROUP BY install_id
    `;

    const cumulativeCounts = SETUP_STEP_ORDER.map((_, index) =>
      stepProgress.filter((row) => (row.max_step ?? -1) >= index).length,
    );

    const steps = SETUP_STEP_ORDER.map((step, index) => {
      const uniqueInstalls = cumulativeCounts[index] ?? 0;
      const prevUnique = index > 0 ? cumulativeCounts[index - 1] ?? 0 : null;
      const dropOffFromPrevious =
        prevUnique != null && prevUnique > 0
          ? Math.round((1 - uniqueInstalls / prevUnique) * 1000) / 10
          : null;
      return {
        step,
        views: uniqueInstalls,
        uniqueInstalls,
        dropOffFromPrevious,
      };
    });

    const handoffStats = await this.prisma.$queryRaw<
      Array<{ handoffs: bigint; claimed: bigint }>
    >`
      SELECT
        COUNT(*)::bigint AS handoffs,
        COUNT(claimed_install_id)::bigint AS claimed
      FROM analytics_attribution_handoffs
      WHERE created_at >= ${since}
    `;

    const claimToSetupStarted = await this.prisma.$queryRaw<Array<{ count: bigint }>>`
      SELECT COUNT(DISTINCT e.install_id)::bigint AS count
      FROM analytics_attribution_handoffs h
      INNER JOIN product_analytics_events e
        ON e.install_id = h.claimed_install_id
        AND e.source = 'app'
        AND e.name = 'setup_started'
        AND e.occurred_at >= ${since}
      WHERE h.created_at >= ${since}
        AND h.claimed_install_id IS NOT NULL
    `;

    const claimToCompleted = await this.prisma.$queryRaw<Array<{ count: bigint }>>`
      SELECT COUNT(DISTINCT e.install_id)::bigint AS count
      FROM analytics_attribution_handoffs h
      INNER JOIN product_analytics_events e
        ON e.install_id = h.claimed_install_id
        AND e.source = 'app'
        AND e.name = 'setup_completed'
        AND e.occurred_at >= ${since}
      WHERE h.created_at >= ${since}
        AND h.claimed_install_id IS NOT NULL
    `;

    const attributionCampaignRows = await this.prisma.$queryRaw<
      Array<{ campaign: string; handoffs: bigint; claimed: bigint; completed: bigint }>
    >`
      SELECT
        COALESCE(h.properties->>'utm_campaign', '(none)') AS campaign,
        COUNT(*)::bigint AS handoffs,
        COUNT(h.claimed_install_id)::bigint AS claimed,
        COUNT(c.install_id)::bigint AS completed
      FROM analytics_attribution_handoffs h
      LEFT JOIN product_analytics_events c
        ON c.install_id = h.claimed_install_id
        AND c.source = 'app'
        AND c.name = 'setup_completed'
        AND c.occurred_at >= ${since}
      WHERE h.created_at >= ${since}
      GROUP BY 1
      ORDER BY handoffs DESC
      LIMIT 25
    `;

    const setupStarted = countFor('setup_started', 'app');
    const setupCompleted = countFor('setup_completed', 'app');

    return {
      enabled: true,
      periodDays,
      web: {
        pageViews:
          events.find((e) => e.name === 'landing_page_view' && e.source === 'web')?.count ?? 0,
        uniqueVisitors: countFor('landing_page_view', 'web'),
        ctaClicks:
          (events.find((e) => e.name === 'cta_click' && e.source === 'web')?.count ?? 0) +
          (events.find((e) => e.name === 'outbound_click' && e.source === 'web')?.count ?? 0),
        outboundClicks:
          events.find((e) => e.name === 'outbound_click' && e.source === 'web')?.count ?? 0,
        events: events.filter((e) => e.source === 'web'),
        ctaByLocation: ctaRows.map((row) => ({
          location: row.location,
          count: Number(row.count),
          uniqueVisitors: Number(row.unique_visitors),
        })),
        audiences: audienceRows.map((row) => ({
          audience: row.audience,
          uniqueVisitors: Number(row.unique_visitors),
        })),
        campaigns: campaignRows.map((row) => ({
          campaign: row.campaign,
          source: row.source,
          pageViews: Number(row.page_views),
          uniqueVisitors: Number(row.unique_visitors),
        })),
      },
      app: {
        setupStarted,
        setupCompleted,
        setupAbandoned: countFor('setup_abandoned', 'app'),
        billingActivated: countFor('setup_billing_activated', 'app'),
        events: events.filter((e) => e.source === 'app'),
        steps,
        completionRate:
          setupStarted > 0
            ? Math.round((setupCompleted / setupStarted) * 1000) / 10
            : null,
      },
      attribution: {
        handoffsCreated: Number(handoffStats[0]?.handoffs ?? 0),
        handoffsClaimed: Number(handoffStats[0]?.claimed ?? 0),
        claimToSetupStarted: Number(claimToSetupStarted[0]?.count ?? 0),
        claimToCompleted: Number(claimToCompleted[0]?.count ?? 0),
        byCampaign: attributionCampaignRows.map((row) => ({
          campaign: row.campaign,
          handoffs: Number(row.handoffs),
          claimed: Number(row.claimed),
          completed: Number(row.completed),
        })),
      },
    };
  }

  private sanitizeProperties(input: Record<string, unknown> | undefined): Record<string, unknown> {
    if (!input || typeof input !== 'object' || Array.isArray(input)) {
      return {};
    }

    const out: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(input)) {
      if (Object.keys(out).length >= MAX_PROPERTY_KEYS) break;
      const normalizedKey = key.trim();
      if (!normalizedKey || BLOCKED_PROPERTY_KEYS.has(normalizedKey)) continue;
      if (/secret|password|token|email|apikey/i.test(normalizedKey)) continue;

      const sanitized = this.sanitizeValue(value);
      if (sanitized !== undefined) {
        out[normalizedKey.slice(0, 64)] = sanitized;
      }
    }
    return out;
  }

  private sanitizeValue(value: unknown): string | number | boolean | null | undefined {
    if (value === null) return null;
    if (typeof value === 'boolean') return value;
    if (typeof value === 'number' && Number.isFinite(value)) return value;
    if (typeof value === 'string') {
      return value.length > MAX_STRING_LENGTH
        ? value.slice(0, MAX_STRING_LENGTH)
        : value;
    }
    return undefined;
  }
}
