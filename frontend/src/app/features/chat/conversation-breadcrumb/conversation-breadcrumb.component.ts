import { Component, Input } from '@angular/core';
import { CommonModule } from '@angular/common';
import { conversationBreadcrumbs } from '../../../core/services/composer-destination.util';
import type { ConversationThread } from '../../../core/services/conversation.service';

@Component({
  selector: 'app-conversation-breadcrumb',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './conversation-breadcrumb.component.html',
  styleUrl: './conversation-breadcrumb.component.scss',
})
export class ConversationBreadcrumbComponent {
  @Input() thread: ConversationThread | null = null;

  get crumbs() {
    return conversationBreadcrumbs(this.thread ?? undefined);
  }
}
