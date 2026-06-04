import { Component, EventEmitter, Input, Output, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ConversationService, ConversationThread } from '../../../core/services/conversation.service';
import { ChatPanelService } from '../../../core/services/chat-panel.service';

@Component({
  selector: 'app-conversation-nav',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './conversation-nav.component.html',
  styleUrl: './conversation-nav.component.scss',
})
export class ConversationNavComponent {
  @Input() currentKey = 'websocket:main';
  @Input() collapsed = false;
  @Output() selectThread = new EventEmitter<string>();
  @Output() newBranch = new EventEmitter<void>();
  @Output() openInbox = new EventEmitter<void>();

  readonly conversations = inject(ConversationService);
  readonly panels = inject(ChatPanelService);

  onSelect(key: string): void {
    this.selectThread.emit(key);
  }

  isActive(key: string): boolean {
    return this.currentKey === key;
  }

  unreadCount(t: ConversationThread): boolean {
    return !!t.unread;
  }
}
