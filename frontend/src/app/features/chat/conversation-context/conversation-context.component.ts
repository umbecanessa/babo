import { Component, Input, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterModule } from '@angular/router';
import type { ConversationThread } from '../../../core/services/conversation.service';

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

  toolsLink(): string[] {
    return this.agentId ? ['/tools', this.agentId] : ['/tools'];
  }

  isHome(): boolean {
    return !this.thread || this.thread.key === 'websocket:main';
  }

  isSurface(): boolean {
    return !!this.thread && this.thread.channel !== 'websocket';
  }

  isGroup(): boolean {
    const k = this.thread?.key || '';
    return k.includes(':group:') || k.includes(':channel:');
  }
}
