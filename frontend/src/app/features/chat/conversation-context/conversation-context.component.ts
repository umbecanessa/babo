import { Component, Input, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterModule } from '@angular/router';
import { ApiService } from '../../../core/services/api.service';
import { ConversationService, type ConversationThread } from '../../../core/services/conversation.service';

@Component({
  selector: 'app-conversation-context',
  standalone: true,
  imports: [CommonModule, RouterModule],
  templateUrl: './conversation-context.component.html',
  styleUrl: './conversation-context.component.scss',
})
export class ConversationContextComponent {
  @Input() thread: ConversationThread | null = null;
  @Input() agentId = '';

  private readonly conversations = inject(ConversationService);
  private readonly api = inject(ApiService);

  reachabilityBusy = signal(false);

  toolsLink(): string[] {
    return this.agentId ? ['/tools', this.agentId] : ['/tools'];
  }

  isHome(): boolean {
    if (!this.thread) return true;
    return this.conversations.isDefaultHome(this.thread.key, this.agentId || undefined);
  }

  isSurface(): boolean {
    return !!this.thread && this.thread.channel !== 'websocket';
  }

  isGroup(): boolean {
    const k = this.thread?.key || '';
    return k.includes(':group:') || k.includes(':channel:');
  }

  canSetPrimaryReachability(): boolean {
    if (!this.thread || !this.agentId) return false;
    if (this.thread.channel === 'team' || this.thread.channel === 'delegate') return false;
    return this.isSurface() || this.conversations.isWebsocketBranch(this.thread.key);
  }

  isPrimaryReachability(): boolean {
    if (!this.thread) return false;
    return this.conversations.isPrimaryReachability(this.thread.key, this.agentId || undefined);
  }

  togglePrimaryReachability(): void {
    if (!this.thread || !this.agentId || this.reachabilityBusy()) return;
    this.reachabilityBusy.set(true);
    const key = this.thread.key;
    if (this.isPrimaryReachability()) {
      this.api.clearPrimaryReachability(this.agentId).subscribe({
        next: (res) => {
          const primary = (res?.primary_reachability_session_key || this.conversations.defaultHomeKey(this.agentId)).trim();
          this.conversations.setPrimaryReachabilityForAgent(this.agentId, primary);
          this.reachabilityBusy.set(false);
        },
        error: () => this.reachabilityBusy.set(false),
      });
      return;
    }
    this.api.setPrimaryReachability(this.agentId, key).subscribe({
      next: (res) => {
        const primary = (res?.primary_reachability_session_key || key).trim();
        if (primary) {
          this.conversations.setPrimaryReachabilityForAgent(this.agentId, primary);
        }
        this.reachabilityBusy.set(false);
      },
      error: () => this.reachabilityBusy.set(false),
    });
  }
}
