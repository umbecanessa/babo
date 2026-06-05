import { Component, OnInit, signal, input, output } from '@angular/core';

import { CommonModule } from '@angular/common';

import { FormsModule } from '@angular/forms';

import {

  ApiService,

  Squad,

  SquadCreate,

  SquadInboxItem,

  SquadKanbanBoard,

  SquadPendingAction,

} from '../../../core/services/api.service';

import { Agent } from '../../../core/models/agent.model';

import { CharterTab } from '../agent-charter-modal/agent-charter-modal.component';

import { AgentCardComponent } from '../agent-card/agent-card.component';

export type SquadConfirmAction = 'delete_squad' | 'remove_member';

export interface SquadConfirmRequest {
  title: string;
  message: string;
  confirmLabel: string;
  variant: 'danger' | 'default';
  action: SquadConfirmAction;
  squadId?: string;
  leadId?: string;
  squad?: Squad;
  memberId?: string;
  /** Checkbox label when action is delete_squad. */
  deleteAgentsOptionLabel?: string;
}

@Component({

  selector: 'app-squads-panel',

  standalone: true,

  imports: [CommonModule, FormsModule, AgentCardComponent],

  templateUrl: './squads-panel.component.html',

  styleUrl: './squads-panel.component.scss',

})

export class SquadsPanelComponent implements OnInit {

  agents = input.required<Agent[]>();

  remoteMode = input(false);

  relayOnline = input<Record<string, boolean>>({});

  deletingAgentId = input<string | null>(null);

  pausingAgentId = input<string | null>(null);

  squadsChanged = output<void>();

  charterRequested = output<{ agentId: string; tab: CharterTab }>();

  agentDelete = output<string>();

  agentTogglePause = output<Agent>();



  squads = signal<Squad[]>([]);

  loading = signal(true);

  error = signal('');

  showCreate = signal(false);

  expandedMember = signal<string | null>(null);



  newName = '';

  newLeadId = '';

  selectedMembers: string[] = [];

  addMemberPick: Record<string, string> = {};



  editingSquad = signal<Squad | null>(null);

  kanbanSquad = signal<Squad | null>(null);

  kanbanBoard = signal<SquadKanbanBoard | null>(null);

  kanbanLoading = signal(false);

  confirmDialog = signal<SquadConfirmRequest | null>(null);

  checkbackEnabled = true;

  checkbackIntervalMin = 30;

  proposalSlaHours = 4;



  constructor(private api: ApiService) {}



  ngOnInit(): void {

    this.loadSquads();

  }



  loadSquads(): void {

    this.loading.set(true);

    this.api.listSquads().subscribe({

      next: res => {

        this.squads.set(res.squads || []);

        this.loading.set(false);

      },

      error: err => {

        this.error.set(err?.error?.detail || err?.message || 'Failed to load squads');

        this.loading.set(false);

      },

    });

  }



  get agentOptions(): Agent[] {

    return this.agents().filter(a => !a.squadId && !a.runtime?.squad_id);

  }



  isAgentOnline(agentId: string): boolean {

    if (!this.remoteMode()) return true;

    return this.relayOnline()[agentId] ?? false;

  }



  agentFor(runtimeOrDbId: string): Agent | undefined {

    return this.agents().find(

      x => x.runtimeAgentId === runtimeOrDbId || x.id === runtimeOrDbId,

    );

  }



  memberIdsOrdered(sq: Squad): string[] {

    const ids = [...(sq.member_agent_ids || [])];

    if (sq.lead_agent_id && !ids.includes(sq.lead_agent_id)) {

      ids.unshift(sq.lead_agent_id);

    }

    return ids;

  }



  memberExpandKey(squadId: string, memberId: string): string {

    return `${squadId}:${memberId}`;

  }



  isMemberExpanded(squadId: string, memberId: string): boolean {

    return this.expandedMember() === this.memberExpandKey(squadId, memberId);

  }



  toggleMemberExpand(squadId: string, memberId: string): void {

    const key = this.memberExpandKey(squadId, memberId);

    this.expandedMember.update(cur => (cur === key ? null : key));

  }



  toggleMember(id: string): void {

    if (this.selectedMembers.includes(id)) {

      this.selectedMembers = this.selectedMembers.filter(m => m !== id);

    } else {

      this.selectedMembers = [...this.selectedMembers, id];

    }

  }



  createSquad(): void {

    if (!this.newName.trim() || !this.newLeadId) return;

    const body: SquadCreate = {

      name: this.newName.trim(),

      lead_agent_id: this.newLeadId,

      member_agent_ids: this.selectedMembers.filter(id => id !== this.newLeadId),

    };

    this.api.createSquad(body).subscribe({

      next: () => {

        this.showCreate.set(false);

        this.newName = '';

        this.newLeadId = '';

        this.selectedMembers = [];

        this.loadSquads();

        this.squadsChanged.emit();

      },

      error: err => {

        this.error.set(err?.error?.detail || err?.message || 'Create failed');

      },

    });

  }



  addMemberToSquad(sq: Squad): void {

    const pick = this.addMemberPick[sq.id];

    if (!pick) return;

    const members = [...(sq.member_agent_ids || [])];

    if (members.includes(pick)) return;

    members.push(pick);

    this.api.updateSquad(sq.id, { member_agent_ids: members }, sq.lead_agent_id).subscribe({

      next: () => {

        this.addMemberPick[sq.id] = '';

        this.loadSquads();

        this.squadsChanged.emit();

      },

      error: err => {

        this.error.set(err?.error?.detail || err?.message || 'Add member failed');

      },

    });

  }



  cancelConfirm(): void {
    this.confirmDialog.set(null);
  }

  executeConfirm(result: { optionChecked: boolean } = { optionChecked: false }): void {
    const dialog = this.confirmDialog();
    if (!dialog) return;
    this.confirmDialog.set(null);
    if (dialog.action === 'delete_squad' && dialog.squadId && dialog.leadId) {
      this.performDeleteSquad(dialog.squadId, dialog.leadId, result.optionChecked);
    } else if (dialog.action === 'remove_member' && dialog.squad && dialog.memberId) {
      this.performRemoveMember(dialog.squad, dialog.memberId);
    }
  }

  removeMemberFromSquad(sq: Squad, memberId: string): void {
    const label = this.agentLabel(memberId);
    const isLead = memberId === sq.lead_agent_id;

    if (isLead) {
      const others = (sq.member_agent_ids || []).filter(id => id !== memberId);
      if (others.length === 0) {
        this.error.set('Cannot remove the only member. Delete the squad instead.');
        return;
      }
    }

    const message = isLead
      ? `Remove ${label} from the squad? Lead role will transfer to another member.`
      : `Remove ${label} from this squad?`;

    this.confirmDialog.set({
      title: isLead ? 'Transfer lead and remove member?' : 'Remove from squad?',
      message,
      confirmLabel: isLead ? 'Transfer and remove' : 'Remove member',
      variant: 'danger',
      action: 'remove_member',
      squad: sq,
      memberId,
    });
  }

  private performRemoveMember(sq: Squad, memberId: string): void {
    const isLead = memberId === sq.lead_agent_id;

    let body: Partial<SquadCreate> & { lead_agent_id?: string };

    if (isLead) {

      const others = (sq.member_agent_ids || []).filter(id => id !== memberId);

      if (others.length === 0) {

        this.error.set('Cannot remove the only member. Delete the squad instead.');

        return;

      }

      body = {

        lead_agent_id: others[0],

        member_agent_ids: others.filter(id => id !== others[0]),

      };

    } else {

      body = {

        member_agent_ids: (sq.member_agent_ids || []).filter(id => id !== memberId),

      };

    }



    this.api.updateSquad(sq.id, body, sq.lead_agent_id).subscribe({

      next: () => {

        if (this.isMemberExpanded(sq.id, memberId)) {

          this.expandedMember.set(null);

        }

        this.loadSquads();

        this.squadsChanged.emit();

      },

      error: err => {

        this.error.set(err?.error?.detail || err?.message || 'Remove failed');

      },

    });

  }



  pendingActions(sq: Squad): SquadPendingAction[] {

    return (sq.pending_actions || []).filter(pa => pa.status === 'pending');

  }



  pendingActionLabel(pa: SquadPendingAction): string {

    if (pa.action_type === 'delete_agent') {

      return `Delete agent ${this.agentLabel(pa.target_agent_id || '')}`;

    }

    if (pa.action_type === 'patch_trust') {

      return `Update trust for ${this.agentLabel(pa.target_agent_id || '')}`;

    }

    if (pa.action_type === 'patch_job') {

      return `Update job for ${this.agentLabel(pa.target_agent_id || '')}`;

    }

    return pa.title || pa.action_type;

  }



  resolvePendingAction(sq: Squad, actionId: string, approved: boolean): void {

    this.api.resolveSquadPendingAction(sq.id, actionId, approved).subscribe({

      next: () => {

        this.loadSquads();

        this.squadsChanged.emit();

      },

      error: err => {

        this.error.set(err?.error?.detail || err?.message || 'Action failed');

      },

    });

  }



  openKanban(sq: Squad): void {

    this.kanbanSquad.set(sq);

    this.kanbanBoard.set(null);

    this.kanbanLoading.set(true);

    this.api.getSquadKanban(sq.id, sq.lead_agent_id).subscribe({

      next: board => {

        this.kanbanBoard.set(board);

        this.kanbanLoading.set(false);

      },

      error: err => {

        this.error.set(err?.error?.detail || err?.message || 'Failed to load board');

        this.kanbanLoading.set(false);

        this.kanbanSquad.set(null);

      },

    });

  }



  closeKanban(): void {

    this.kanbanSquad.set(null);

    this.kanbanBoard.set(null);

  }



  memberIdsForKanban(sq: Squad): string[] {

    const ids = new Set<string>(sq.member_agent_ids || []);

    ids.add(sq.lead_agent_id);

    return [...ids];

  }



  deleteSquad(sq: Squad): void {
    const memberIds = this.memberIdsOrdered(sq);
    const n = memberIds.length;
    this.confirmDialog.set({
      title: 'Delete squad?',
      message:
        'By default, agents stay on your fleet as independents — only the shared squad inbox and coordination context are removed.',
      confirmLabel: 'Delete squad',
      variant: 'danger',
      action: 'delete_squad',
      squadId: sq.id,
      leadId: sq.lead_agent_id,
      deleteAgentsOptionLabel:
        `Also delete all ${n} agent${n === 1 ? '' : 's'} in this squad (permanent)`,
    });
  }

  private performDeleteSquad(id: string, leadId: string, deleteAgents: boolean): void {
    this.api.deleteSquad(id, leadId, deleteAgents).subscribe({
      next: () => {
        this.loadSquads();
        this.squadsChanged.emit();
      },
      error: err => {
        this.error.set(err?.error?.detail || err?.message || 'Delete squad failed');
      },
    });
  }



  agentLabel(id: string): string {

    const a = this.agents().find(x => x.runtimeAgentId === id || x.id === id);

    return a?.name || id.slice(0, 8);

  }



  openCharter(agentId: string, tab: CharterTab = 'job'): void {

    this.charterRequested.emit({ agentId, tab });

  }



  openSquadSettings(sq: Squad): void {

    this.editingSquad.set(sq);

    this.checkbackEnabled = sq.checkback_enabled !== false;

    this.checkbackIntervalMin = Math.round(

      (sq.checkback_interval_seconds ?? 1800) / 60,

    );

    this.proposalSlaHours = Math.round(

      (sq.proposal_sla_seconds ?? 14400) / 3600,

    );

  }



  saveSquadSettings(): void {

    const sq = this.editingSquad();

    if (!sq) return;

    this.api.updateSquad(

      sq.id,

      {

        checkback_enabled: this.checkbackEnabled,

        checkback_interval_seconds: Math.max(5, this.checkbackIntervalMin) * 60,

        proposal_sla_seconds: Math.max(1, this.proposalSlaHours) * 3600,

      },

      sq.lead_agent_id,

    ).subscribe({

      next: () => {

        this.editingSquad.set(null);

        this.loadSquads();

      },

      error: err => {

        this.error.set(err?.error?.detail || err?.message || 'Update failed');

      },

    });

  }



  cancelSquadSettings(): void {

    this.editingSquad.set(null);

  }



  pendingCount(sq: Squad): number {

    return (sq.inbox || []).filter(

      (i: SquadInboxItem) => i.status === 'proposed',

    ).length;

  }

}


