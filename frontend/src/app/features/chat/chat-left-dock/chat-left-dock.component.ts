import { Component, EventEmitter, Input, Output, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ChatPanelService } from '../../../core/services/chat-panel.service';
import { ChatWorkbenchComponent } from '../chat-workbench/chat-workbench.component';
import { AgentBrowserComponent } from '../agent-browser/agent-browser.component';
import { TranslateModule } from '@ngx-translate/core';

@Component({
  selector: 'app-chat-left-dock',
  standalone: true,
  imports: [CommonModule, ChatWorkbenchComponent, AgentBrowserComponent, TranslateModule],
  templateUrl: './chat-left-dock.component.html',
  styleUrl: './chat-left-dock.component.scss',
})
export class ChatLeftDockComponent {
  @Input() agentId = '';
  @Input() browserCommand: any = null;

  @Output() browserResult = new EventEmitter<any>();
  @Output() collapseBrowser = new EventEmitter<void>();

  readonly panels = inject(ChatPanelService);

  selectTab(tab: 'workbench' | 'browser'): void {
    this.panels.toggleLeft(tab);
  }
}
