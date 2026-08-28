import { Component, Input, Output, EventEmitter, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { TranslateModule, TranslateService } from '@ngx-translate/core';
import { Agent } from '../../../core/models/agent.model';
import {
  buildAgentSnapshot,
  AgentSnapshot,
  AgentVital,
  vitalHint,
  formatRelativeTime,
  formatAbsoluteTime,
} from '../../../core/services/agent-snapshot.util';

@Component({
  selector: 'app-agent-card',
  standalone: true,
  imports: [CommonModule, RouterLink, TranslateModule],
  templateUrl: './agent-card.component.html',
  styleUrl: './agent-card.component.scss',
  host: { '[class.accordion-host]': 'accordion' },
})
export class AgentCardComponent {
  private readonly translate = inject(TranslateService);
  @Input({ required: true }) agent!: Agent;
  @Input() online = true;
  @Input() remoteMode = false;
  @Input() deleting = false;
  @Input() pausing = false;
  @Input() accordion = false;
  @Input() expanded = false;
  @Input() showRemoveFromSquad = false;
  @Input() isSquadLeadMember = false;
  @Output() delete = new EventEmitter<void>();
  @Output() togglePause = new EventEmitter<void>();
  @Output() editCharter = new EventEmitter<{ agentId: string; tab: 'job' | 'trust' }>();
  @Output() accordionToggle = new EventEmitter<void>();
  @Output() removeFromSquad = new EventEmitter<void>();

  onAccordionHeaderClick(event: MouseEvent): void {
    const target = event.target as HTMLElement;
    if (target.closest('button, a, input, select, textarea')) return;
    this.accordionToggle.emit();
  }

  get snapshot(): AgentSnapshot {
    return buildAgentSnapshot(this.agent);
  }

  vitalHint(vital: AgentVital): string {
    return vitalHint(vital, this.agent.runtime?.heartbeat);
  }

  get isPaused(): boolean {
    return this.agent.userPaused === true;
  }

  get status(): string {
    if (this.isPaused) return 'paused';
    if (this.remoteMode && !this.online) return 'offline';
    if (this.snapshot.isBusy && this.agent.runtime?.activity?.user_busy) return 'working';
    if (this.agent.runtime?.consciousness?.inner_loop?.active_dreaming) return 'dreaming';
    return this.agent.runtime?.status || this.agent.status || 'offline';
  }

  get statusLabel(): string {
    if (this.isPaused) return this.translate.instant('dashboard.agent.paused');
    if (this.remoteMode && !this.online) return this.translate.instant('dashboard.agent.desktopOffline');
    switch (this.status) {
      case 'alive': return this.translate.instant('dashboard.agent.online');
      case 'sleeping': return this.translate.instant('dashboard.agent.sleeping');
      case 'chatting': return this.translate.instant('dashboard.agent.chatting');
      case 'working': return this.translate.instant('dashboard.agent.working');
      case 'dreaming': return this.translate.instant('dashboard.agent.dreaming');
      case 'offline': return this.translate.instant('dashboard.agent.offline');
      case 'unreachable': return this.translate.instant('dashboard.agent.unreachable');
      default: return this.status;
    }
  }

  get statusColor(): string {
    if (this.isPaused) return 'var(--text-muted)';
    if (this.remoteMode && !this.online) return 'var(--accent-danger)';
    switch (this.status) {
      case 'alive': return 'var(--accent-success)';
      case 'sleeping': return 'var(--accent-warn)';
      case 'chatting': return 'var(--accent-primary)';
      case 'working': return 'var(--accent-primary)';
      case 'dreaming': return '#c084fc';
      case 'offline': return 'var(--text-muted)';
      default: return '#525252';
    }
  }

  get jobTitle(): string {
    return (
      this.agent.jobTitle
      || this.agent.runtime?.job_title
      || this.translate.instant('dashboard.agent.defaultJob')
    );
  }

  get displayName(): string {
    return this.agent.name || this.agent.runtimeAgentId?.substring(0, 8) || this.agent.id?.substring(0, 8) || 'Agent';
  }

  get createdLabel(): string | null {
    return formatRelativeTime(this.agent.createdAt);
  }

  get createdTitle(): string | null {
    return formatAbsoluteTime(this.agent.createdAt);
  }

  get lastActiveLabel(): string | null {
    return formatRelativeTime(this.agent.runtime?.last_interaction);
  }

  get lastActiveTitle(): string | null {
    return formatAbsoluteTime(this.agent.runtime?.last_interaction);
  }
}
