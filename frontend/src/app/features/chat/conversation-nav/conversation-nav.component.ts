import {
  Component,
  EventEmitter,
  HostListener,
  Input,
  Output,
  inject,
  signal,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ConversationService, ConversationThread } from '../../../core/services/conversation.service';
import { ChatPanelService } from '../../../core/services/chat-panel.service';
import { ToastService } from '../../../shared/toast/toast.service';
import { ConfirmDialogComponent } from '../../../shared/confirm-dialog/confirm-dialog.component';
import { ThreadConfirmRequest } from '../../../shared/thread-dialog/thread-dialog.types';
import { TranslateModule, TranslateService } from '@ngx-translate/core';

@Component({
  selector: 'app-conversation-nav',
  standalone: true,
  imports: [CommonModule, FormsModule, ConfirmDialogComponent, TranslateModule],
  templateUrl: './conversation-nav.component.html',
  styleUrl: './conversation-nav.component.scss',
})
export class ConversationNavComponent {
  @Input() currentKey = 'websocket:main';
  @Input() collapsed = false;
  @Input() agentId = '';
  @Output() selectThread = new EventEmitter<string>();
  @Output() newBranch = new EventEmitter<void>();
  @Output() openInbox = new EventEmitter<void>();
  @Output() branchRenamed = new EventEmitter<{ key: string; label: string }>();
  @Output() deleteBranchConfirmed = new EventEmitter<string>();
  @Output() promoteToHomeConfirmed = new EventEmitter<string>();
  @Output() resetHomeConfirmed = new EventEmitter<void>();

  branchMenuKey: string | null = null;
  renamingKey: string | null = null;
  renameDraft = '';
  confirmDialog = signal<ThreadConfirmRequest | null>(null);

  readonly conversations = inject(ConversationService);
  readonly panels = inject(ChatPanelService);
  private readonly toast = inject(ToastService);
  private readonly translate = inject(TranslateService);

  private t(key: string, params?: Record<string, unknown>): string {
    return this.translate.instant(key, params);
  }

  @HostListener('document:click')
  closeBranchMenu(): void {
    this.branchMenuKey = null;
  }

  onSelect(key: string): void {
    if (this.renamingKey) return;
    this.selectThread.emit(key);
  }

  isActive(key: string): boolean {
    return this.currentKey === key;
  }

  isRenaming(key: string): boolean {
    return this.renamingKey === key;
  }

  unreadCount(t: ConversationThread): boolean {
    return !!t.unread;
  }

  isBranch(t: ConversationThread): boolean {
    return this.conversations.isWebsocketBranch(t.key);
  }

  toggleBranchMenu(event: MouseEvent, key: string): void {
    event.stopPropagation();
    this.branchMenuKey = this.branchMenuKey === key ? null : key;
  }

  onRenameBranch(key: string, event: MouseEvent): void {
    event.stopPropagation();
    this.branchMenuKey = null;
    if (!this.conversations.isWebsocketBranch(key)) return;
    const current = this.conversations.threads().find((t) => t.key === key);
    this.renamingKey = key;
    this.renameDraft = current?.label || 'Branch';
  }

  updateRenameDraft(value: string): void {
    this.renameDraft = value;
  }

  onRenameKeydown(event: KeyboardEvent, key: string): void {
    if (event.key === 'Enter') {
      event.preventDefault();
      this.commitRename(key);
    } else if (event.key === 'Escape') {
      event.preventDefault();
      this.cancelRename();
    }
  }

  onRenameBlur(key: string): void {
    queueMicrotask(() => {
      if (this.renamingKey !== key) return;
      this.commitRename(key);
    });
  }

  private commitRename(key: string): void {
    const trimmed = this.renameDraft.trim();
    this.renamingKey = null;
    if (!trimmed) return;
    this.conversations.renameBranch(key, trimmed);
    this.branchRenamed.emit({ key, label: trimmed });
  }

  cancelRename(): void {
    this.renamingKey = null;
    this.renameDraft = '';
  }

  onDeleteBranch(key: string, event: MouseEvent): void {
    event.stopPropagation();
    this.branchMenuKey = null;
    if (!this.conversations.isWebsocketBranch(key)) return;
    if (this.conversations.isDefaultHome(key, this.agentId)) {
      this.toast.show(this.t('chat.nav.cannotDeleteHome'), 'error');
      return;
    }
    const current = this.conversations.threads().find((t) => t.key === key);
    this.confirmDialog.set({
      title: this.t('chat.nav.deleteBranchTitle'),
      message: this.t('chat.nav.deleteBranchMessage', { label: current?.label || key }),
      confirmLabel: this.t('chat.nav.delete'),
      variant: 'danger',
      action: 'delete_branch',
      sessionKey: key,
    });
  }

  onPromoteToHome(key: string, event: MouseEvent): void {
    event.stopPropagation();
    this.branchMenuKey = null;
    if (this.conversations.isDefaultHome(key, this.agentId)) return;
    const label = this.conversations.displayLabel(
      this.conversations.threads().find((t) => t.key === key)
        || { key, label: key, channel: 'websocket' },
      this.agentId,
    );
    this.confirmDialog.set({
      title: this.t('chat.nav.setAsHome'),
      message: this.t('chat.nav.setAsHomeMessage', { label }),
      confirmLabel: this.t('chat.nav.setAsHome'),
      variant: 'default',
      action: 'promote_home',
      sessionKey: key,
    });
  }

  onResetHome(event: MouseEvent): void {
    event.stopPropagation();
    this.branchMenuKey = null;
    this.confirmDialog.set({
      title: this.t('chat.nav.resetHome'),
      message: this.t('chat.nav.resetHomeMessage'),
      confirmLabel: this.t('chat.nav.resetHome'),
      variant: 'default',
      action: 'reset_home',
      sessionKey: '',
    });
  }

  executeConfirm(): void {
    const req = this.confirmDialog();
    if (!req) return;
    this.confirmDialog.set(null);
    switch (req.action) {
      case 'delete_branch':
        this.deleteBranchConfirmed.emit(req.sessionKey);
        break;
      case 'promote_home':
        this.promoteToHomeConfirmed.emit(req.sessionKey);
        break;
      case 'reset_home':
        this.resetHomeConfirmed.emit();
        break;
    }
  }

  cancelConfirm(): void {
    this.confirmDialog.set(null);
  }
}
