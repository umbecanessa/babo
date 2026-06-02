import { Component, Input, Output, EventEmitter } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
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
  imports: [CommonModule, RouterLink],
  templateUrl: './agent-card.component.html',
  styleUrl: './agent-card.component.scss',
})
export class AgentCardComponent {
  @Input({ required: true }) agent!: Agent;
  @Input() online = true;
  @Input() remoteMode = false;
  @Input() deleting = false;
  @Input() pausing = false;
  @Output() delete = new EventEmitter<void>();
  @Output() togglePause = new EventEmitter<void>();

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
    if (this.isPaused) return 'Paused';
    if (this.remoteMode && !this.online) return 'Desktop Offline';
    switch (this.status) {
      case 'alive': return 'Online';
      case 'sleeping': return 'Sleeping';
      case 'chatting': return 'Chatting';
      case 'working': return 'Working';
      case 'dreaming': return 'Daydreaming';
      case 'offline': return 'Offline';
      case 'unreachable': return 'Unreachable';
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
