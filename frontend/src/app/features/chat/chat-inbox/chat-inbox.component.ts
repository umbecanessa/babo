import { Component, EventEmitter, Input, Output, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ConversationService, InboxItem } from '../../../core/services/conversation.service';
import { TranslateModule, TranslateService } from '@ngx-translate/core';

@Component({
  selector: 'app-chat-inbox',
  standalone: true,
  imports: [CommonModule, TranslateModule],
  templateUrl: './chat-inbox.component.html',
  styleUrl: './chat-inbox.component.scss',
})
export class ChatInboxComponent {
  @Output() selectSession = new EventEmitter<string>();

  readonly conversations = inject(ConversationService);
  private readonly translate = inject(TranslateService);

  sections = this.conversations.inboxSections;

  open(item: InboxItem): void {
    this.selectSession.emit(item.sessionKey);
  }

  kindLabel(kind: string): string {
    switch (kind) {
      case 'ask_user': return this.translate.instant('chat.inbox.kind.needsYou');
      case 'skipped': return this.translate.instant('chat.inbox.kind.skipped');
      case 'failed': return this.translate.instant('chat.inbox.kind.failed');
      case 'unread':
      case 'inbound': return this.translate.instant('chat.inbox.kind.unread');
      default: return this.translate.instant('chat.inbox.kind.recent');
    }
  }
}
