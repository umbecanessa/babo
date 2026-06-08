import { Component, Input, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterModule } from '@angular/router';
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
}
