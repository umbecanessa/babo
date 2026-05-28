import {
  Agent,
  AgentActivityStatus,
  AgentConsciousnessStatus,
  HeartbeatStatus,
  WorkingMemoryStatus,
} from '../models/agent.model';

export type ActivityKind =
  | 'plan'
  | 'team'
  | 'chat'
  | 'orchestration'
  | 'dream'
  | 'sleep'
  | 'idle'
  | 'paused'
  | 'offline';

export interface AgentVital {
  key: string;
  label: string;
  value: string;
  tone?: 'model' | 'focus' | 'mood' | 'time' | 'energy' | 'neutral';
}

export interface AgentSnapshot {
  activityKind: ActivityKind;
  activityKindLabel: string;
  activityHeadline: string;
  activityHeadlineFull: string;
  activityDetail: string | null;
  activityDetailFull: string | null;
  vitals: AgentVital[];
  isBusy: boolean;
  isWorking: boolean;
  statsLine: string;
  energyPercent: number | null;
  /** True when the card should show the highlighted activity panel (not idle/offline). */
  showActivityPanel: boolean;
}

const FOREGROUND_LABELS: Record<string, string> = {
  user: 'Chat turn',
  channel: 'Channel message',
  scheduler: 'Autonomous work',
  dmn: 'Daydreaming',
  drives: 'Drive action',
  team_checkback: 'Team check-in',
  delegate_batch_complete: 'Team follow-up',
  idle: 'Idle',
};

const NETWORK_LABELS: Record<string, string> = {
  ECN: 'Focused',
  SN: 'Alert',
  DMN: 'Reflecting',
  TRANSITION: 'Switching focus',
};

const NETWORK_HINTS: Record<string, string> = {
  ECN: 'Executive control — task-focused',
  SN: 'Salience — monitoring for change',
  DMN: 'Default mode — reflecting or idle',
  TRANSITION: 'Brain network handoff',
};

const MOOD_HINTS: Record<string, string> = {
  calm: 'Low arousal, steady',
  tense: 'Elevated arousal',
  alert: 'High vigilance',
  agitated: 'High arousal, unsettled',
  focused: 'Engaged on task',
  tired: 'Low energy',
  curious: 'Exploratory drive',
};

function truncate(text: string, max: number): string {
  const s = (text || '').trim();
  if (s.length <= max) return s;
  return `${s.slice(0, max - 1)}…`;
}

function titleCase(text: string): string {
  const s = (text || '').trim();
  if (!s) return s;
  return s.charAt(0).toUpperCase() + s.slice(1).toLowerCase();
}

function shortModelId(model: string | null | undefined): string | null {
  const raw = (model || '').trim();
  if (!raw) return null;
  const slash = raw.lastIndexOf('/');
  const tail = slash >= 0 ? raw.slice(slash + 1) : raw;
  return tail.replace(/^models\//, '');
}

function formatRelativeTime(iso: string | undefined): string | null {
  if (!iso) return null;
  const ts = Date.parse(iso);
  if (Number.isNaN(ts)) return null;
  const deltaSec = Math.max(0, Math.floor((Date.now() - ts) / 1000));
  if (deltaSec < 60) return 'just now';
  if (deltaSec < 3600) return `${Math.floor(deltaSec / 60)}m ago`;
  if (deltaSec < 86400) return `${Math.floor(deltaSec / 3600)}h ago`;
  return `${Math.floor(deltaSec / 86400)}d ago`;
}

function networkLabelFromHeartbeat(hb: HeartbeatStatus | undefined): string | null {
  if (!hb) return null;
  const raw = (hb.dominant_network || '').toString().toUpperCase();
  return NETWORK_LABELS[raw] || (raw ? titleCase(raw) : null);
}

function networkHintFromHeartbeat(hb: HeartbeatStatus | undefined): string | null {
  if (!hb) return null;
  const raw = (hb.dominant_network || '').toString().toUpperCase();
  return NETWORK_HINTS[raw] || 'Current brain network mode';
}

function moodHint(mood: string | null): string | null {
  if (!mood) return null;
  return MOOD_HINTS[mood.toLowerCase()] || 'Emotional state';
}

function shortTeamId(teamId: string | undefined): string | null {
  const raw = (teamId || '').trim();
  if (!raw) return null;
  const tail = raw.length > 8 ? raw.slice(-6).toUpperCase() : raw.toUpperCase();
  return `…${tail}`;
}

function humanizeForegroundSource(source: string): {
  kind: ActivityKind;
  kindLabel: string;
  headline: string;
  detail: string | null;
} {
  const raw = (source || '').trim();
  const lower = raw.toLowerCase();

  if (lower.startsWith('team_member_escalation:')) {
    const [, teamId] = raw.split(':');
    return {
      kind: 'team',
      kindLabel: 'Team',
      headline: 'Handling member escalation',
      detail: shortTeamId(teamId) ? `Team ${shortTeamId(teamId)}` : null,
    };
  }
  if (lower.startsWith('team_completion_review:')) {
    const parts = raw.split(':');
    const teamId = parts[1];
    const phase = parts[2] ? titleCase(parts[2].replace(/_/g, ' ')) : null;
    return {
      kind: 'team',
      kindLabel: 'Team',
      headline: 'Reviewing team completion',
      detail: [shortTeamId(teamId) ? `Team ${shortTeamId(teamId)}` : null, phase].filter(Boolean).join(' · ') || null,
    };
  }
  if (lower.startsWith('team_wave_complete:')) {
    const [, teamId] = raw.split(':');
    return {
      kind: 'team',
      kindLabel: 'Team',
      headline: 'Team wave finished',
      detail: shortTeamId(teamId) ? `Team ${shortTeamId(teamId)}` : null,
    };
  }
  if (lower.startsWith('team_checkback:')) {
    const [, teamId] = raw.split(':');
    return {
      kind: 'team',
      kindLabel: 'Team',
      headline: 'Team check-in',
      detail: shortTeamId(teamId) ? `Team ${shortTeamId(teamId)}` : null,
    };
  }
  if (lower.startsWith('pending_wave_launch:')) {
    return {
      kind: 'team',
      kindLabel: 'Team',
      headline: 'Launching team wave',
      detail: null,
    };
  }
  if (lower.startsWith('delegate')) {
    return {
      kind: 'orchestration',
      kindLabel: 'Working',
      headline: 'Delegate follow-up',
      detail: null,
    };
  }
  if (lower.startsWith('drive:')) {
    const drive = raw.split(':')[1]?.replace(/_/g, ' ') || 'internal drive';
    return {
      kind: 'orchestration',
      kindLabel: 'Working',
      headline: 'Acting on drive',
      detail: titleCase(drive),
    };
  }
  if (lower.startsWith('scheduler')) {
    return {
      kind: 'orchestration',
      kindLabel: 'Working',
      headline: 'Scheduled autonomous work',
      detail: null,
    };
  }

  const mapped = FOREGROUND_LABELS[lower];
  if (mapped) {
    const kind: ActivityKind =
      lower === 'user' || lower === 'channel' ? 'chat'
        : lower === 'dmn' ? 'dream'
          : 'orchestration';
    return {
      kind,
      kindLabel: kind === 'chat' ? 'Chat' : kind === 'dream' ? 'Dreaming' : 'Working',
      headline: mapped,
      detail: null,
    };
  }

  if (raw && lower !== 'idle') {
    return {
      kind: 'orchestration',
      kindLabel: 'Working',
      headline: 'Background work',
      detail: truncate(raw.replace(/_/g, ' '), 48),
    };
  }

  return {
    kind: 'idle',
    kindLabel: 'Idle',
    headline: 'Standing by',
    detail: null,
  };
}

function parsePlanPosition(raw: string): { step: string | null; task: string } {
  const text = (raw || '').trim();
  const stepMatch = text.match(/(\d+\s*\/\s*\d+)\s*steps?\s*done/i);
  const step = stepMatch ? stepMatch[1].replace(/\s/g, '') : null;

  let task = text
    .replace(/^\[PLAN POSITION[^\]]*\]\s*/i, '')
    .replace(/^Task:\s*/i, '')
    .trim();
  if (!task) task = text;

  return { step, task };
}

function pickGoalHeadline(wm: WorkingMemoryStatus | undefined): string | null {
  if (!wm) return null;
  const goals = wm.goals || [];
  const tactical = goals.find(g => (g.level || '').toLowerCase() === 'tactical');
  const strategic = goals.find(g => (g.level || '').toLowerCase() === 'strategic');
  const pick = tactical || strategic || goals[0];
  const content = (pick?.content || '').trim();
  return content || null;
}

function teamHeadline(wm: WorkingMemoryStatus | undefined): { headline: string; detail: string | null } | null {
  const teams = wm?.orch_teams;
  if (!teams?.length) return null;
  const active = teams.find(t => {
    const s = (t.status || '').toLowerCase();
    return s && s !== 'done' && s !== 'disbanded' && s !== 'failed';
  }) || teams[0];
  const done = Number(active.done_count || 0);
  const total = Number(active.member_count || 0);
  const status = titleCase((active.status || 'active').toString());
  const teamRef = shortTeamId(active.team_id);
  if (total > 0) {
    return {
      headline: `Team progress ${done}/${total}`,
      detail: [teamRef ? `Team ${teamRef}` : null, status].filter(Boolean).join(' · '),
    };
  }
  return {
    headline: 'Team orchestration',
    detail: teamRef ? `Team ${teamRef} · ${status}` : status,
  };
}

function energyPercentFromHeartbeat(hb: HeartbeatStatus | undefined): number | null {
  const energy = hb?.energy;
  if (energy == null || Number.isNaN(energy)) return null;
  return Math.round(Math.max(0, Math.min(1, energy)) * 100);
}

export function buildAgentSnapshot(agent: Agent): AgentSnapshot {
  const rt = agent.runtime;
  const hb = rt?.heartbeat;
  const wm = rt?.working_memory;
  const ans = rt?.ans;
  const activity = rt?.activity as AgentActivityStatus | undefined;
  const consciousness = rt?.consciousness as AgentConsciousnessStatus | undefined;
  const inner = consciousness?.inner_loop;

  const isBusy = activity?.busy === true;
  const userBusy = activity?.user_busy === true;
  const foregroundSource = (activity?.foreground_source || 'idle').toString();
  const activeDreaming = inner?.active_dreaming === true;
  const consciousnessState = (consciousness?.state || '').toLowerCase();

  const facts = rt?.facts_in_memory ?? 0;
  const turns = rt?.turn_count ?? 0;
  const statsLine = `${facts} facts · ${turns} turn${turns === 1 ? '' : 's'}`;

  const moodRaw = (hb?.mood_label || '').trim() || null;
  const moodLabel = moodRaw ? titleCase(moodRaw) : null;
  const networkLabel = networkLabelFromHeartbeat(hb);
  const networkHint = networkHintFromHeartbeat(hb);
  const modelLabel =
    shortModelId(rt?.orchestrator_model)
    || shortModelId(activity?.orchestrator_model);
  const lastActiveLabel = formatRelativeTime(rt?.last_interaction);
  const energyPercent = energyPercentFromHeartbeat(hb);

  const agentStatus = (agent.runtime?.status || agent.status || 'offline').toLowerCase();
  const runtimeLoaded = Boolean(rt && (rt.turn_count != null || rt.facts_in_memory != null || rt.heartbeat));

  let activityKind: ActivityKind = 'idle';
  let activityKindLabel = 'Idle';
  let activityHeadline = 'Standing by';
  let activityHeadlineFull = activityHeadline;
  let activityDetail: string | null = null;
  let activityDetailFull: string | null = null;

  if (agent.userPaused) {
    activityKind = 'paused';
    activityKindLabel = 'Paused';
    activityHeadline = 'Paused by you';
  } else if (!runtimeLoaded && (agentStatus === 'offline' || agentStatus === 'unreachable')) {
    activityKind = 'offline';
    activityKindLabel = 'Offline';
    activityHeadline = 'Runtime offline';
    activityDetail = 'Start desktop runtime to load';
  } else if (userBusy) {
    activityKind = 'chat';
    activityKindLabel = 'Chat';
    activityHeadline = 'Working on your request';
    const fg = humanizeForegroundSource(foregroundSource);
    activityDetail = fg.detail || fg.headline;
  } else if (isBusy) {
    const fg = humanizeForegroundSource(foregroundSource);
    activityKind = fg.kind;
    activityKindLabel = fg.kindLabel;
    activityHeadline = fg.headline;
    activityDetail = fg.detail;
  } else if (activeDreaming) {
    activityKind = 'dream';
    activityKindLabel = 'Dreaming';
    activityHeadline = 'Daydreaming';
  } else if (ans?.state === 'sleeping') {
    activityKind = 'sleep';
    activityKindLabel = 'Sleep';
    activityHeadline = 'Consolidating memories';
  } else if (consciousnessState === 'sleeping') {
    activityKind = 'sleep';
    activityKindLabel = 'Sleep';
    activityHeadline = 'Background sleep';
  } else if (wm?.plan_position) {
    const parsed = parsePlanPosition(String(wm.plan_position));
    activityKind = 'plan';
    activityKindLabel = 'Plan';
    activityHeadlineFull = parsed.task;
    activityHeadline = truncate(parsed.task, 72);
    activityDetail = parsed.step ? `Step ${parsed.step}` : 'Plan in progress';
    activityDetailFull = String(wm.plan_position);
  } else if (teamHeadline(wm)) {
    const team = teamHeadline(wm)!;
    activityKind = 'team';
    activityKindLabel = 'Team';
    activityHeadline = team.headline;
    activityDetail = team.detail;
  } else if (pickGoalHeadline(wm)) {
    const goal = pickGoalHeadline(wm)!;
    activityKind = 'orchestration';
    activityKindLabel = 'Goal';
    activityHeadlineFull = goal;
    activityHeadline = truncate(goal, 72);
    activityDetail = 'Current goal';
    activityDetailFull = goal;
  } else if (hb?.felt_idle) {
    const felt = String(hb.felt_idle);
    activityKind = 'idle';
    activityKindLabel = 'Idle';
    activityHeadlineFull = felt;
    activityHeadline = truncate(felt, 72);
  } else if (networkLabel || moodLabel) {
    activityKind = 'idle';
    activityKindLabel = 'Idle';
    activityHeadline = networkLabel || `Feeling ${moodLabel}`;
    activityDetail = networkLabel && moodLabel ? `Mood: ${moodLabel}` : null;
  }

  activityHeadlineFull = activityHeadlineFull || activityHeadline;
  activityDetailFull = activityDetailFull || activityDetail;

  const vitals: AgentVital[] = [];
  if (modelLabel) {
    vitals.push({ key: 'model', label: 'Model', value: modelLabel, tone: 'model' });
  }
  if (networkLabel) {
    vitals.push({
      key: 'focus',
      label: 'Focus',
      value: networkLabel,
      tone: 'focus',
    });
  }
  if (moodLabel) {
    vitals.push({
      key: 'mood',
      label: 'Mood',
      value: moodLabel,
      tone: 'mood',
    });
  }
  if (energyPercent != null) {
    vitals.push({
      key: 'energy',
      label: 'Energy',
      value: `${energyPercent}%`,
      tone: 'energy',
    });
  }
  if (lastActiveLabel) {
    vitals.push({
      key: 'time',
      label: 'Active',
      value: lastActiveLabel,
      tone: 'time',
    });
  }

  const hasOpenWork = Boolean(
    wm?.plan_position
    || (wm?.orch_teams?.length ?? 0) > 0
    || pickGoalHeadline(wm),
  );
  const isWorking = isBusy || activeDreaming || hasOpenWork;
  const isGenericIdle = activityHeadline === 'Standing by';
  const showActivityPanel = Boolean(
    isWorking
    || activityDetail
    || vitals.length > 0
    || (!isGenericIdle && !agent.userPaused),
  );

  return {
    activityKind,
    activityKindLabel,
    activityHeadline,
    activityHeadlineFull,
    activityDetail,
    activityDetailFull,
    vitals,
    isBusy,
    isWorking,
    statsLine,
    energyPercent,
    showActivityPanel,
  };
}

export function vitalHint(vital: AgentVital, hb?: HeartbeatStatus): string {
  switch (vital.key) {
    case 'focus':
      return networkHintFromHeartbeat(hb) || 'Brain network mode';
    case 'mood':
      return moodHint(vital.value) || 'Emotional state';
    case 'model':
      return 'Orchestrator model for this agent';
    case 'energy':
      return 'Cognitive energy reserve';
    case 'time':
      return 'Last interaction';
    default:
      return vital.label;
  }
}
