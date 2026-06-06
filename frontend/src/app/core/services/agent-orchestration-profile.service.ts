import { Injectable, computed, signal } from '@angular/core';

/** User-facing orchestration depth for the next message. */
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

@Injectable({ providedIn: 'root' })
export class AgentOrchestrationProfileService {
  /** Per-agent one-shot override for the next outgoing message. */
  private readonly overrideByAgent = new Map<string, OrchestrationProfileChoice>();
  private readonly epoch = signal(0);

  /** Last profile chosen by triage (server). */
  readonly lastTriageProfile = signal<string | null>(null);

  readonly activeAgentId = signal<string | null>(null);

  readonly options = ORCHESTRATION_PROFILE_OPTIONS;

  setActiveAgent(agentId: string | null): void {
    this.activeAgentId.set(agentId);
  }

  choiceFor(agentId: string | null): OrchestrationProfileChoice {
    this.epoch();
    if (!agentId) return 'auto';
    return this.overrideByAgent.get(agentId) ?? 'auto';
  }

  setChoice(agentId: string, choice: OrchestrationProfileChoice): void {
    this.overrideByAgent.set(agentId, choice);
    this.epoch.update(v => v + 1);
  }

  profileForOutgoingMessage(agentId: string | null): string | undefined {
    const choice = this.choiceFor(agentId);
    if (choice === 'auto') return undefined;
    return choice;
  }

  triggerLabel(agentId: string | null): string {
    const choice = this.choiceFor(agentId);
    if (choice !== 'auto') {
      const opt = ORCHESTRATION_PROFILE_OPTIONS.find(o => o.id === choice);
      return opt?.label ?? choice;
    }
    const triaged = this.lastTriageProfile();
    if (triaged) {
      const short = PROFILE_SHORT[triaged] ?? triaged;
      return `Auto · ${short}`;
    }
    return 'Auto';
  }

  readonly hasManualOverride = computed(() => {
    this.epoch();
    const id = this.activeAgentId();
    if (!id) return false;
    return (this.overrideByAgent.get(id) ?? 'auto') !== 'auto';
  });

  noteTriageProfile(profile: string | null | undefined): void {
    const p = (profile || '').trim();
    this.lastTriageProfile.set(p || null);
  }

  shortLabel(profile: string | null | undefined): string {
    const p = (profile || '').trim();
    return PROFILE_SHORT[p] ?? p ?? '—';
  }
}
