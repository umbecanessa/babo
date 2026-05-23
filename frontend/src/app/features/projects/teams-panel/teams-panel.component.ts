import { Component, Input } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Team, Timeline, TimelineWave, PlanStepSummary } from '../project.models';
import { ProjectService } from '../project.service';

interface WaveCard {
  waveIndex: number;
  label: string;
  state: 'active' | 'completed' | 'failed' | 'planned';
  steps: PlanStepSummary[];
  team: Team | null;
  progress: { done: number; running: number; failed: number; total: number };
  elapsed: number;
}

@Component({
  selector: 'app-teams-panel',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './teams-panel.component.html',
  styleUrl: './teams-panel.component.scss',
})
export class TeamsPanelComponent {
  @Input() teams: Team[] = [];
  @Input() timeline: Timeline | null = null;
  @Input() agentId = '';

  expandedMembers = new Set<string>();
  hintTarget: { teamId: string; memberIdx: number } | null = null;
  hintText = '';

  constructor(private svc: ProjectService) {}

  get waves(): WaveCard[] {
    const teamsByWave = new Map<number, Team>();
    for (const t of this.teams) {
      const existing = teamsByWave.get(t.wave_index);
      if (!existing || t.created_at > existing.created_at) {
        teamsByWave.set(t.wave_index, t);
      }
    }

    if (this.timeline?.waves?.length) {
      return this.timeline.waves.map(tw => this.buildWaveCard(tw, teamsByWave.get(tw.wave_index) ?? null));
    }

    const sorted = [...teamsByWave.entries()].sort(([a], [b]) => a - b);
    return sorted.map(([idx, team]) => this.buildWaveCardFromTeam(idx, team));
  }

  private buildWaveCard(tw: TimelineWave, team: Team | null): WaveCard {
    const state = this.resolveWaveState(team, tw);
    const progress = team
      ? this.memberProgress(team)
      : { done: 0, running: 0, failed: 0, total: tw.steps.length };
    const elapsed = team ? this.teamElapsed(team) : 0;

    return {
      waveIndex: tw.wave_index,
      label: `Wave ${tw.wave_index + 1}`,
      state,
      steps: tw.steps,
      team,
      progress,
      elapsed,
    };
  }

  private buildWaveCardFromTeam(waveIndex: number, team: Team): WaveCard {
    const state = this.teamToWaveState(team);
    return {
      waveIndex,
      label: `Wave ${waveIndex + 1}`,
      state,
      steps: team.members.map(m => ({
        id: m.step_id || `m-${m.delegate_number}`,
        label: m.task,
        status: this.memberStatusToPlanStatus(m.status),
        depends_on: [],
        delegatable: true,
      })),
      team,
      progress: this.memberProgress(team),
      elapsed: this.teamElapsed(team),
    };
  }

  private resolveWaveState(team: Team | null, tw: TimelineWave): WaveCard['state'] {
    if (!team) {
      if (tw.team?.status === 'completed') return 'completed';
      return 'planned';
    }
    return this.teamToWaveState(team);
  }

  private teamToWaveState(team: Team): WaveCard['state'] {
    if (['completed', 'partial'].includes(team.status)) return 'completed';
    if (team.status === 'failed') return 'failed';
    return 'active';
  }

  private memberProgress(team: Team) {
    const members = team.members || [];
    return {
      done: members.filter(m => m.status === 'done').length,
      running: members.filter(m => m.status === 'running').length,
      failed: members.filter(m => m.status === 'failed').length,
      total: members.length,
    };
  }

  private teamElapsed(team: Team): number {
    if (team.completed_at && team.created_at) return team.completed_at - team.created_at;
    if (team.created_at) return (Date.now() / 1000) - team.created_at;
    return 0;
  }

  private memberStatusToPlanStatus(status: string): PlanStepSummary['status'] {
    switch (status) {
      case 'running': return 'in_progress';
      case 'done': return 'done';
      case 'failed': return 'failed';
      case 'cancelled': return 'skipped';
      default: return 'pending';
    }
  }

  // ── Template helpers ──────────────────────────────

  waveAccent(state: WaveCard['state']): string {
    switch (state) {
      case 'active': return '#818cf8';
      case 'completed': return '#34d399';
      case 'failed': return '#f87171';
      case 'planned': return '#4b5563';
    }
  }

  progressPct(wave: WaveCard): number {
    if (!wave.progress.total) return 0;
    return Math.round((wave.progress.done / wave.progress.total) * 100);
  }

  memberStatusColor(status: string): string {
    switch (status) {
      case 'running': return '#818cf8';
      case 'done': return '#34d399';
      case 'failed': return '#f87171';
      case 'cancelled': return '#6b7280';
      default: return '#374151';
    }
  }

  memberIcon(status: string): string {
    switch (status) {
      case 'running': return '▶';
      case 'done': return '✓';
      case 'failed': return '✗';
      case 'cancelled': return '—';
      default: return '○';
    }
  }

  stepIcon(status: string): string {
    switch (status) {
      case 'done': return '✓';
      case 'in_progress': return '▶';
      case 'failed': return '✗';
      case 'skipped': return '—';
      default: return '○';
    }
  }

  stepStatusColor(status: string): string {
    switch (status) {
      case 'done': return '#34d399';
      case 'in_progress': return '#818cf8';
      case 'failed': return '#f87171';
      default: return '#374151';
    }
  }

  formatElapsed(seconds: number): string {
    if (!seconds || seconds <= 0) return '';
    if (seconds < 60) return `${Math.round(seconds)}s`;
    const mins = Math.floor(seconds / 60);
    if (mins < 60) return `${mins}m ${Math.round(seconds % 60)}s`;
    const hrs = Math.floor(mins / 60);
    return `${hrs}h ${mins % 60}m`;
  }

  formatDuration(seconds: number): string {
    if (!seconds || seconds <= 0) return '—';
    return this.formatElapsed(seconds);
  }

  taskLabel(task: string): string {
    if (!task) return 'Untitled';
    return task.length > 50 ? task.slice(0, 50) + '…' : task;
  }

  toggleMember(teamId: string, idx: number): void {
    const key = `${teamId}:${idx}`;
    if (this.expandedMembers.has(key)) {
      this.expandedMembers.delete(key);
    } else {
      this.expandedMembers.add(key);
    }
  }

  isMemberExpanded(teamId: string, idx: number): boolean {
    return this.expandedMembers.has(`${teamId}:${idx}`);
  }

  openHint(teamId: string, memberIdx: number): void {
    this.hintTarget = { teamId, memberIdx };
    this.hintText = '';
  }

  sendHint(): void {
    if (!this.hintTarget || !this.hintText.trim()) return;
    this.svc.hintMember(this.hintTarget.teamId, this.hintTarget.memberIdx, this.hintText.trim());
    this.hintTarget = null;
    this.hintText = '';
  }

  cancelHint(): void {
    this.hintTarget = null;
    this.hintText = '';
  }

  onPause(teamId: string): void { this.svc.pauseTeam(teamId); }
  onResume(teamId: string): void { this.svc.resumeTeam(teamId); }
  onDisband(teamId: string): void { this.svc.disbandTeam(teamId); }
  onAdvance(teamId: string): void { this.svc.advanceTeam(teamId); }

  onForceStart(waveIndex: number): void {
    const planId = this.timeline?.plan_id;
    if (planId) this.svc.forceStartWave(planId, waveIndex);
  }
}
