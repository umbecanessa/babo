import { Component, EventEmitter, Input, Output, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ConversationService, InboxItem } from '../../../core/services/conversation.service';

@Component({
  selector: 'app-chat-inbox',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './chat-inbox.component.html',
  styleUrl: './chat-inbox.component.scss',
})
export class ChatInboxComponent {
  @Output() selectSession = new EventEmitter<string>();

  readonly conversations = inject(ConversationService);

  sections = this.conversations.inboxSections;

  open(item: InboxItem): void {
    this.selectSession.emit(item.sessionKey);
  }

  kindLabel(kind: string): string {
    switch (kind) {
      case 'ask_user': return 'Needs you';
      case 'skipped': return 'Skipped';
      case 'failed': return 'Failed';
      case 'unread':
      case 'inbound': return 'Unread';
      default: return 'Recent';
    }
  }
}
