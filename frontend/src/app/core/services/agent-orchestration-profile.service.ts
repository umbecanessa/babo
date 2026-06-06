import { Injectable, computed, signal } from '@angular/core';
import { formatAgentMode } from './workbench-labels.util';

/** User-facing orchestration depth for outgoing messages. */
export type OrchestrationProfileChoice =
  | 'auto'
  | 'conversational'
  | 'solo_structured'
  | 'orchestrated';

export interface OrchestrationProfileOption {
  id: OrchestrationProfileChoice;
  label: string;
  description: string;
}

export const ORCHESTRATION_PROFILE_OPTIONS: OrchestrationProfileOption[] = [
  {
    id: 'auto',
    label: 'Auto',
    description: 'Triage picks depth; upgrades to EM when a plan + teams exist.',
  },
  {
    id: 'conversational',
    label: 'Chat',
    description: 'Quick answers and light tools — no plan/team waves.',
  },
  {
    id: 'solo_structured',
    label: 'Solo',
    description: 'You execute with plan/todo/bash — no delegate waves.',
  },
  {
    id: 'orchestrated',
    label: 'EM',
    description: 'Full engineering manager: plan, team, delegate waves.',
  },
];

const PROFILE_SHORT: Record<string, string> = {
  conversational: 'Chat',
  solo_structured: 'Solo',
  orchestrated: 'EM',
  squad_lead: 'Squad',
};

export interface TriageProfileNote {
  profile?: string | null;
  requested?: string | null;
  effective?: string | null;
  floored?: boolean;
}

@Injectable({ providedIn: 'root' })
export class AgentOrchestrationProfileService {
  /** Per-agent sticky override until user picks Auto again. */
  private readonly overrideByAgent = new Map<string, OrchestrationProfileChoice>();
  private readonly triageProfileByAgent = new Map<string, string>();
  private readonly profileRequestedByAgent = new Map<string, string>();
  private readonly profileEffectiveByAgent = new Map<string, string>();
  private readonly profileFlooredByAgent = new Map<string, boolean>();
  /** Live orchestrator mode (planning, executing, …) during agentic runs. */
  private readonly runtimeModeByAgent = new Map<string, string>();
  private readonly agenticActiveByAgent = new Map<string, boolean>();
  /** Bump when profile/mode/triage state changes (for reactive UI). */
  readonly revision = signal(0);
  private readonly epoch = this.revision;

  readonly activeAgentId = signal<string | null>(null);

  readonly options = ORCHESTRATION_PROFILE_OPTIONS;

  setActiveAgent(agentId: string | null): void {
    this.activeAgentId.set(agentId);
    this.epoch.update(v => v + 1);
  }

  choiceFor(agentId: string | null): OrchestrationProfileChoice {
    this.epoch();
    if (!agentId) return 'auto';
    return this.overrideByAgent.get(agentId) ?? 'auto';
  }

  setChoice(agentId: string, choice: OrchestrationProfileChoice): void {
    this.overrideByAgent.set(agentId, choice);
    this.profileFlooredByAgent.delete(agentId);
    this.epoch.update(v => v + 1);
  }

  profileForOutgoingMessage(agentId: string | null): string | undefined {
    const choice = this.choiceFor(agentId);
    if (choice === 'auto') return undefined;
    return choice;
  }

  lastTriageProfile(agentId: string | null): string | null {
    this.epoch();
    if (!agentId) return null;
    return this.triageProfileByAgent.get(agentId) ?? null;
  }

  isProfileFloored(agentId: string | null): boolean {
    this.epoch();
    if (!agentId) return false;
    return this.profileFlooredByAgent.get(agentId) ?? false;
  }

  isAgenticActive(agentId: string | null): boolean {
    this.epoch();
    if (!agentId) return false;
    return this.agenticActiveByAgent.get(agentId) ?? false;
  }

  runtimeMode(agentId: string | null): string | null {
    this.epoch();
    if (!agentId) return null;
    return this.runtimeModeByAgent.get(agentId) ?? null;
  }

  setAgenticActive(agentId: string | null | undefined, active: boolean): void {
    const id = (agentId ?? '').trim();
    if (!id) return;
    if (active) {
      this.agenticActiveByAgent.set(id, true);
      if (!this.runtimeModeByAgent.has(id)) {
        this.runtimeModeByAgent.set(id, 'planning');
      }
    } else {
      this.agenticActiveByAgent.delete(id);
      this.runtimeModeByAgent.delete(id);
    }
    this.epoch.update(v => v + 1);
  }

  setRuntimeMode(agentId: string | null | undefined, mode: string | null | undefined): void {
    const id = (agentId ?? '').trim();
    const next = (mode ?? '').trim().toLowerCase();
    if (!id || !next) return;
    this.runtimeModeByAgent.set(id, next);
    this.epoch.update(v => v + 1);
  }

  /** Depth/profile portion only (Auto, Solo, Auto · EM, Solo → EM). */
  depthLabel(agentId: string | null): string {
    const choice = this.choiceFor(agentId);
    if (choice !== 'auto') {
      const opt = ORCHESTRATION_PROFILE_OPTIONS.find(o => o.id === choice);
      const base = opt?.label ?? choice;
      if (this.isProfileFloored(agentId)) {
        const effective = this.profileEffectiveByAgent.get(agentId ?? '');
        const short = PROFILE_SHORT[effective ?? ''] ?? effective ?? 'EM';
        return `${base} → ${short}`;
      }
      return base;
    }
    const triaged = this.lastTriageProfile(agentId);
    if (triaged) {
      const short = PROFILE_SHORT[triaged] ?? triaged;
      return `Auto · ${short}`;
    }
    return 'Auto';
  }

  /** Runtime mode label when agentic task is active; null when idle. */
  modeLabel(agentId: string | null): string | null {
    if (!this.isAgenticActive(agentId)) return null;
    const mode = this.runtimeMode(agentId) || 'planning';
    return formatAgentMode(mode);
  }

  triggerLabel(agentId: string | null): string {
    const depth = this.depthLabel(agentId);
    const mode = this.modeLabel(agentId);
    return mode ? `${depth} · ${mode}` : depth;
  }

  triggerTitle(agentId: string | null): string {
    const choice = this.choiceFor(agentId);
    const triaged = this.lastTriageProfile(agentId);
    const mode = this.modeLabel(agentId);
    if (choice !== 'auto' && this.isProfileFloored(agentId)) {
      const requested = this.profileRequestedByAgent.get(agentId ?? '') ?? choice;
      const effective = this.profileEffectiveByAgent.get(agentId ?? '') ?? triaged;
      const base = (
        `Requested ${requested}; active plan requires ${effective ?? 'orchestrated'}.`
      );
      return mode ? `${base} Mode: ${mode}.` : base;
    }
    if (mode) {
      return `Orchestration: ${this.depthLabel(agentId)} — ${mode}`;
    }
    if (triaged) {
      return `Orchestration: ${this.depthLabel(agentId)} (last triage: ${triaged})`;
    }
    return `Orchestration depth: ${this.depthLabel(agentId)}`;
  }

  readonly hasManualOverride = computed(() => {
    this.epoch();
    const id = this.activeAgentId();
    if (!id) return false;
    return (this.overrideByAgent.get(id) ?? 'auto') !== 'auto';
  });

  noteTriageProfile(
    agentId: string | null | undefined,
    note: TriageProfileNote | string | null | undefined,
  ): void {
    const id = (agentId ?? '').trim();
    if (!id) return;

    const payload: TriageProfileNote =
      typeof note === 'string' || note == null
        ? { profile: note }
        : note;

    const p = (payload.profile ?? '').trim();
    if (p) {
      this.triageProfileByAgent.set(id, p);
    }

    const requested = (payload.requested ?? '').trim();
    const effective = (payload.effective ?? p).trim();
    if (requested) {
      this.profileRequestedByAgent.set(id, requested);
    }
    if (effective) {
      this.profileEffectiveByAgent.set(id, effective);
    }

    if (payload.floored) {
      this.profileFlooredByAgent.set(id, true);
    } else if (
      requested &&
      effective &&
      requested !== 'auto' &&
      requested !== effective
    ) {
      this.profileFlooredByAgent.set(id, true);
    } else if (!payload.floored && !requested) {
      this.profileFlooredByAgent.delete(id);
    }

    this.epoch.update(v => v + 1);
  }

  shortLabel(profile: string | null | undefined): string {
    const p = (profile || '').trim();
    return PROFILE_SHORT[p] ?? p ?? '—';
  }
}
