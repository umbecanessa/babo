import { Component, Input, OnChanges, SimpleChanges } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { TranslateModule } from '@ngx-translate/core';
import { Team, TeamMember, Timeline, TimelineWave, PlanStepSummary } from '../project.models';
import { ProjectService } from '../project.service';

interface WaveAttemptView {
  team: Team;
  state: 'active' | 'completed' | 'failed' | 'awaiting_launch' | 'planned';
  progress: { done: number; running: number; failed: number; total: number };
  elapsed: number;
}

interface WaveCard {
  waveIndex: number;
  label: string;
  state: 'active' | 'completed' | 'failed' | 'planned' | 'awaiting_launch';
  steps: PlanStepSummary[];
  attempts: WaveAttemptView[];
  /** Latest attempt for header actions */
  primaryTeam: Team | null;
  progress: { done: number; running: number; failed: number; total: number };
  elapsed: number;
}

@Component({
  selector: 'app-teams-panel',
  standalone: true,
  imports: [CommonModule, FormsModule, TranslateModule],
  templateUrl: './teams-panel.component.html',
  styleUrl: './teams-panel.component.scss',
})
export class TeamsPanelComponent implements OnChanges {
  @Input() teams: Team[] = [];
  @Input() timeline: Timeline | null = null;
  @Input() agentId = '';

  expandedMembers = new Set<string>();

  ngOnChanges(changes: SimpleChanges): void {
    if (!changes['teams']) return;
    for (const team of this.teams) {
      team.members.forEach((m, i) => {
        if (m.status === 'running') {
          this.expandedMembers.add(`${team.id}:${i}`);
        }
      });
    }
  }
  expandedAttempts = new Set<string>();
  hintTarget: { teamId: string; memberIdx: number } | null = null;
  hintText = '';

  constructor(private svc: ProjectService) {}

  get waves(): WaveCard[] {
    const byWave = new Map<number, Team[]>();
    for (const t of this.teams) {
      const list = byWave.get(t.wave_index) ?? [];
      list.push(t);
      byWave.set(t.wave_index, list);
    }
    for (const [idx, list] of byWave) {
      list.sort((a, b) => (b.created_at ?? 0) - (a.created_at ?? 0));
      byWave.set(idx, list);
    }

    if (this.timeline?.waves?.length) {
      return this.timeline.waves.map(tw => {
        const attemptTeams = byWave.get(tw.wave_index) ?? [];
        return this.buildWaveCard(tw, attemptTeams);
      });
    }

    const sorted = [...byWave.entries()].sort(([a], [b]) => a - b);
    return sorted.map(([idx, attemptTeams]) =>
      this.buildWaveCardFromTeams(idx, attemptTeams),
    );
  }

  private buildWaveCard(tw: TimelineWave, attemptTeams: Team[]): WaveCard {
    const attempts = attemptTeams.map(t => this.buildAttemptView(t));
    const primary = this.pickPrimaryTeam(attemptTeams);
    const state = this.resolveWaveCardState(attemptTeams, primary, tw);
    const progress = primary
      ? this.memberProgress(primary)
      : { done: 0, running: 0, failed: 0, total: tw.steps.length };
    const elapsed = primary ? this.teamElapsed(primary) : 0;

    return {
      waveIndex: tw.wave_index,
      label: `Wave ${tw.wave_index + 1}`,
      state,
      steps: tw.steps.map(s => ({
        ...s,
        status: this.displayStepStatus(s.status, state),
      })),
      attempts,
      primaryTeam: primary,
      progress,
      elapsed,
    };
  }

  private buildWaveCardFromTeams(waveIndex: number, attemptTeams: Team[]): WaveCard {
    const attempts = attemptTeams.map(t => this.buildAttemptView(t));
    const primary = this.pickPrimaryTeam(attemptTeams);
    const state = this.resolveWaveCardState(attemptTeams, primary, null);
    return {
      waveIndex,
      label: `Wave ${waveIndex + 1}`,
      state,
      steps: primary?.members.map(m => ({
        id: m.step_id || `m-${m.delegate_number}`,
        label: m.task,
        status: this.memberStatusToPlanStatus(m.status),
        depends_on: [],
        delegatable: true,
      })) ?? [],
      attempts,
      primaryTeam: primary,
      progress: primary ? this.memberProgress(primary) : { done: 0, running: 0, failed: 0, total: 0 },
      elapsed: primary ? this.teamElapsed(primary) : 0,
    };
  }

  /** Prefer the running attempt over a newer cancelled/failed duplicate. */
  private pickPrimaryTeam(attemptTeams: Team[]): Team | null {
    if (!attemptTeams.length) return null;
    const active = attemptTeams.find(t => t.status === 'active' || t.status === 'paused');
    if (active) return active;
    const completed = attemptTeams.find(t => ['completed', 'partial'].includes(t.status));
    if (completed) return completed;
    const meaningful = attemptTeams.find(t => t.status !== 'cancelled');
    return meaningful ?? attemptTeams[0];
  }

  private resolveWaveCardState(
    attemptTeams: Team[],
    primary: Team | null,
    tw: TimelineWave | null,
  ): WaveCard['state'] {
    if (attemptTeams.some(t => t.status === 'active' || t.status === 'paused')) {
      return 'active';
    }
    if (attemptTeams.some(t => ['completed', 'partial'].includes(t.status))) {
      return 'completed';
    }
    if (!primary) {
      if (tw?.team?.status === 'completed') return 'completed';
      return 'planned';
    }
    return this.teamToWaveState(primary);
  }

  private buildAttemptView(team: Team): WaveAttemptView {
    return {
      team,
      state: this.teamToWaveState(team),
      progress: this.memberProgress(team),
      elapsed: this.teamElapsed(team),
    };
  }

  attemptLabel(attempt: WaveAttemptView): string {
    const n = attempt.team.wave_attempt ?? 1;
    if (attempt.team.status === 'cancelled') {
      return n <= 1 ? `${attempt.team.name} (cancelled)` : `Attempt ${n} (cancelled)`;
    }
    if (n <= 1) {
      return attempt.team.name;
    }
    return `Attempt ${n}: ${attempt.team.name}`;
  }

  toggleAttempt(teamId: string): void {
    if (this.expandedAttempts.has(teamId)) {
      this.expandedAttempts.delete(teamId);
    } else {
      this.expandedAttempts.add(teamId);
    }
  }

  isAttemptExpanded(teamId: string): boolean {
    return this.expandedAttempts.has(teamId);
  }

  private teamToWaveState(team: Team): WaveCard['state'] {
    if (team.status === 'created') return 'awaiting_launch';
    if (team.status === 'cancelled') return 'planned';
    if (['completed', 'partial'].includes(team.status)) return 'completed';
    if (team.status === 'failed') return 'failed';
    if (team.status === 'active' || team.status === 'paused') return 'active';
    return 'planned';
  }

  private memberProgress(team: Team) {
    const done = team.members.filter(m => m.status === 'done').length;
    const running = team.members.filter(m => m.status === 'running').length;
    const failed = team.members.filter(
      m => m.status === 'failed' || m.status === 'cancelled',
    ).length;
    return { done, running, failed, total: team.members.length };
  }

  private teamElapsed(team: Team): number {
    if (!team.created_at) return 0;
    const end = team.completed_at || Date.now() / 1000;
    return Math.max(0, end - team.created_at);
  }

  private memberStatusToPlanStatus(
    status: string,
  ): 'pending' | 'in_progress' | 'done' | 'skipped' | 'failed' {
    if (status === 'done') return 'done';
    if (status === 'running') return 'in_progress';
    if (status === 'failed' || status === 'cancelled') return 'failed';
    return 'pending';
  }

  waveAccent(state: WaveCard['state']): string {
    switch (state) {
      case 'active': return 'var(--accent-primary)';
      case 'completed': return 'var(--accent-success)';
      case 'failed': return 'var(--accent-danger)';
      case 'awaiting_launch': return 'var(--accent-warn)';
      default: return '#94a3b8';
    }
  }

  progressPct(wave: WaveCard): number {
    if (!wave.progress.total) return 0;
    return Math.round((wave.progress.done / wave.progress.total) * 100);
  }

  memberStatusColor(status: string): string {
    switch (status) {
      case 'running': return 'var(--accent-primary)';
      case 'done': return 'var(--accent-success)';
      case 'failed':
      case 'cancelled': return 'var(--accent-danger)';
      default: return '#94a3b8';
    }
  }

  memberIcon(status: string): string {
    switch (status) {
      case 'done': return '✓';
      case 'failed':
      case 'cancelled': return '✗';
      case 'running': return '…';
      default: return '○';
    }
  }

  stepStatusColor(status: string): string {
    switch (status) {
      case 'done': return 'var(--accent-success)';
      case 'in_progress': return 'var(--accent-primary)';
      case 'failed': return 'var(--accent-danger)';
      default: return '#94a3b8';
    }
  }

  stepIcon(status: string): string {
    switch (status) {
      case 'done': return '✓';
      case 'in_progress': return '›';
      case 'failed': return '✗';
      default: return '○';
    }
  }

  /** Plan steps marked in_progress before their wave runs show as pending. */
  displayStepStatus(
    status: string,
    waveState: WaveCard['state'],
  ): PlanStepSummary['status'] {
    if (waveState !== 'active' && status === 'in_progress') {
      return 'pending';
    }
    return status as PlanStepSummary['status'];
  }

  taskLabel(task: string): string {
    const line = task.split('\n')[0];
    return line.length > 72 ? line.slice(0, 69) + '…' : line;
  }

  memberStatusLabel(status: string): string {
    switch (status) {
      case 'running': return 'Running';
      case 'done': return 'Done';
      case 'failed': return 'Failed';
      case 'cancelled': return 'Cancelled';
      case 'pending': return 'Pending';
      default: return status;
    }
  }

  recentActions(member: TeamMember): string[] {
    return (member.last_actions ?? []).slice(-5);
  }

  formatDuration(seconds: number): string {
    if (seconds < 60) return `${Math.round(seconds)}s`;
    const m = Math.floor(seconds / 60);
    const s = Math.round(seconds % 60);
    return s > 0 ? `${m}m ${s}s` : `${m}m`;
  }

  formatElapsed(seconds: number): string {
    if (!seconds) return '';
    if (seconds < 60) return `${Math.round(seconds)}s`;
    return `${Math.floor(seconds / 60)}m`;
  }

  isMemberExpanded(teamId: string, memberIdx: number): boolean {
    return this.expandedMembers.has(`${teamId}:${memberIdx}`);
  }

  toggleMember(teamId: string, memberIdx: number): void {
    const key = `${teamId}:${memberIdx}`;
    if (this.expandedMembers.has(key)) {
      this.expandedMembers.delete(key);
    } else {
      this.expandedMembers.add(key);
    }
  }

  openHint(teamId: string, memberIdx: number): void {
    this.hintTarget = { teamId, memberIdx };
    this.hintText = '';
  }

  cancelHint(): void {
    this.hintTarget = null;
    this.hintText = '';
  }

  sendHint(): void {
    if (!this.hintTarget || !this.hintText.trim()) return;
    this.svc.hintMember(
      this.hintTarget.teamId,
      this.hintTarget.memberIdx,
      this.hintText.trim(),
    );
    this.cancelHint();
  }

  onPause(teamId: string): void {
    this.svc.pauseTeam(teamId);
  }

  onResume(teamId: string): void {
    this.svc.resumeTeam(teamId);
  }

  onDisband(teamId: string): void {
    this.svc.disbandTeam(teamId);
  }

  onForceStart(waveIndex: number): void {
    const planId = this.timeline?.plan_id;
    if (planId) this.svc.forceStartWave(planId, waveIndex);
  }

  onSkip(teamId: string): void {
    this.svc.skipWave(teamId);
  }
}
