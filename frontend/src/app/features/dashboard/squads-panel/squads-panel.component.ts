import { Component, OnInit, signal, input, output } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import {
  ApiService,
  Squad,
  SquadCreate,
  SquadInboxItem,
  SquadKanbanBoard,
} from '../../../core/services/api.service';
import { Agent } from '../../../core/models/agent.model';
import {
  AgentCharterModalComponent,
  CharterTab,
} from '../agent-charter-modal/agent-charter-modal.component';

@Component({
  selector: 'app-squads-panel',
  standalone: true,
  imports: [CommonModule, FormsModule, AgentCharterModalComponent],
  templateUrl: './squads-panel.component.html',
  styleUrl: './squads-panel.component.scss',
})
export class SquadsPanelComponent implements OnInit {
  agents = input.required<Agent[]>();
  squadsChanged = output<void>();

  squads = signal<Squad[]>([]);
  loading = signal(true);
  error = signal('');
  showCreate = signal(false);

  newName = '';
  newLeadId = '';
  selectedMembers: string[] = [];

  charterAgentId = signal<string | null>(null);
  charterTab = signal<CharterTab>('job');
  charterVisible = signal(false);

  editingSquad = signal<Squad | null>(null);
  kanbanSquad = signal<Squad | null>(null);
  kanbanBoard = signal<SquadKanbanBoard | null>(null);
  kanbanLoading = signal(false);

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
    return this.agents();
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

  deleteSquad(id: string, leadId: string): void {
    if (!confirm('Delete this squad? Members keep their jobs; squad inbox is removed.')) return;
    this.api.deleteSquad(id, leadId).subscribe({
      next: () => {
        this.loadSquads();
        this.squadsChanged.emit();
      },
    });
  }

  agentLabel(id: string): string {
    const a = this.agents().find(x => x.runtimeAgentId === id || x.id === id);
    return a?.name || id.slice(0, 8);
  }

  openCharter(agentId: string, tab: CharterTab = 'job'): void {
    this.charterAgentId.set(agentId);
    this.charterTab.set(tab);
    this.charterVisible.set(true);
  }

  onCharterDismiss(saved: boolean): void {
    this.charterVisible.set(false);
    this.charterAgentId.set(null);
    if (saved) {
      this.squadsChanged.emit();
      this.loadSquads();
    }
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
