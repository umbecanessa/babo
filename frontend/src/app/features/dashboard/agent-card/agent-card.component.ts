import { Component, Input, Output, EventEmitter } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { Agent } from '../../../core/models/agent.model';

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

  get isPaused(): boolean {
    return this.agent.userPaused === true;
  }

  get status(): string {
    if (this.isPaused) return 'paused';
    if (this.remoteMode && !this.online) return 'offline';
    return this.agent.runtime?.status || this.agent.status || 'offline';
  }

  get statusLabel(): string {
    if (this.isPaused) return 'Paused';
    if (this.remoteMode && !this.online) return 'Desktop Offline';
    switch (this.status) {
      case 'alive': return 'Online';
      case 'sleeping': return 'Sleeping';
      case 'chatting': return 'Chatting';
      case 'offline': return 'Offline';
      case 'unreachable': return 'Unreachable';
      default: return this.status;
    }
  }

  get statusColor(): string {
    if (this.isPaused) return '#8a8a9a';
    if (this.remoteMode && !this.online) return '#ef4444';
    switch (this.status) {
      case 'alive': return '#34d399';
      case 'sleeping': return '#fbbf24';
      case 'chatting': return '#38bdf8';
      case 'offline': return '#8a8a9a';
      default: return '#525252';
    }
  }

  get displayName(): string {
    return this.agent.name || this.agent.runtimeAgentId?.substring(0, 8) || this.agent.id?.substring(0, 8) || 'Agent';
  }

  get factsCount(): number {
    return this.agent.runtime?.facts_in_memory || 0;
  }
}
