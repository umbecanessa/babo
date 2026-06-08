export type ThreadConfirmAction = 'promote_home' | 'reset_home' | 'delete_branch';

export interface ThreadConfirmRequest {
  title: string;
  message: string;
  confirmLabel: string;
  variant: 'danger' | 'default';
  action: ThreadConfirmAction;
  sessionKey: string;
}

export interface ThreadPromptRequest {
  title: string;
  message?: string;
  placeholder?: string;
  value: string;
  confirmLabel: string;
  sessionKey: string;
}
