import { Component, EventEmitter, Input, Output, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ChatPanelService, RightDockTab } from '../../../core/services/chat-panel.service';
import { SignalSidebarComponent } from '../signal-sidebar/signal-sidebar.component';
import { ChatInboxComponent } from '../chat-inbox/chat-inbox.component';
import { ConversationContextComponent } from '../conversation-context/conversation-context.component';
import type { ConversationThread } from '../../../core/services/conversation.service';
import type { Agent } from '../../../core/models/agent.model';

@Component({
  selector: 'app-chat-right-dock',
  standalone: true,
  imports: [
    CommonModule,
    SignalSidebarComponent,
    ChatInboxComponent,
    ConversationContextComponent,
  ],
  templateUrl: './chat-right-dock.component.html',
  styleUrl: './chat-right-dock.component.scss',
})
export class ChatRightDockComponent {
  @Input() metadata: any = null;
  @Input() agent: Agent | null = null;
  @Input() daydreams: any[] = [];
  @Input() activities: any[] = [];
  @Input() connectedChannels: string[] = [];
  @Input() activeThread: ConversationThread | null = null;
  @Input() agentId = '';

  @Output() channelThreadSelect = new EventEmitter<string>();
  @Output() inboxSelect = new EventEmitter<string>();

  readonly panels = inject(ChatPanelService);

  selectTab(tab: RightDockTab): void {
    this.panels.toggleRight(tab);
  }

  onInboxSelect(sessionKey: string): void {
    this.inboxSelect.emit(sessionKey);
  }
}
