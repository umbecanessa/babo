import { Component, Input } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Team, Timeline, TimelineWave } from '../project.models';
import { ProjectService } from '../project.service';

@Component({
  selector: 'app-timeline-panel',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './timeline-panel.component.html',
  styleUrl: './timeline-panel.component.scss',
})
export class TimelinePanelComponent {
  @Input() teams: Team[] = [];
  @Input() timeline: Timeline | null = null;
  @Input() agentId = '';

  constructor(private svc: ProjectService) {}

  get waves(): TimelineWave[] {
    return this.timeline?.waves || [];
  }

  waveStatus(wave: TimelineWave): string {
    if (!wave.team) return 'pending';
    return wave.team.status;
  }

  waveProgressPct(wave: TimelineWave): number {
    if (!wave.team) return 0;
    const parts = wave.team.progress.split('/');
    if (parts.length !== 2) return 0;
    const done = parseInt(parts[0], 10);
    const total = parseInt(parts[1], 10);
    return total > 0 ? Math.round((done / total) * 100) : 0;
  }

  statusColor(status: string): string {
    switch (status) {
      case 'active': return '#818cf8';
      case 'completed': return '#34d399';
      case 'failed': return '#f87171';
      case 'paused': return '#fb923c';
      case 'blocked': return '#fbbf24';
      default: return '#6b7280';
    }
  }

  onSkip(teamId: string): void {
    this.svc.skipWave(teamId);
  }

  onForceStart(waveIndex: number): void {
    if (!this.timeline) return;
    this.svc.forceStartWave(this.timeline.plan_id, waveIndex);
  }
}
