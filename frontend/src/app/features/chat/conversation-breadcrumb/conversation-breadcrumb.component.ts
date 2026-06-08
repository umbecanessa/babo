import { Component, Input, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { conversationBreadcrumbs } from '../../../core/services/composer-destination.util';
import { ConversationService, type ConversationThread } from '../../../core/services/conversation.service';

@Component({
  selector: 'app-conversation-breadcrumb',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './conversation-breadcrumb.component.html',
  styleUrl: './conversation-breadcrumb.component.scss',
})
export class ConversationBreadcrumbComponent {
  @Input() thread: ConversationThread | null = null;
  @Input() agentId = '';

  private readonly conversations = inject(ConversationService);

  get crumbs() {
    const isDefaultHome = !!this.thread
      && this.conversations.isDefaultHome(this.thread.key, this.agentId || undefined);
    return conversationBreadcrumbs(this.thread ?? undefined, isDefaultHome);
  }
}
