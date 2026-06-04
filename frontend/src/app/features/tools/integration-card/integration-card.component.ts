import {
  Component,
  input,
  output,
  computed,
} from '@angular/core';
import { CommonModule } from '@angular/common';

export type IntegrationChannelType = 'email' | 'telegram' | 'whatsapp' | 'google-workspace' | 'discord' | 'slack';

export interface IntegrationStatus {
  connected: boolean;
  displayValue?: string;
}

@Component({
  selector: 'app-integration-card',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="int-card" [class.connected]="status()?.connected" (click)="openDetail.emit()">
      <div class="int-icon" [attr.data-channel]="channel()">
        @switch (channel()) {
          @case ('email') {
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/>
              <polyline points="22,6 12,13 2,6"/>
            </svg>
          }
          @case ('telegram') {
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/>
            </svg>
          }
          @case ('whatsapp') {
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M21 11.5a8.38 8.38 0 01-.9 3.8 8.5 8.5 0 01-7.6 4.7 8.38 8.38 0 01-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 01-.9-3.8 8.5 8.5 0 014.7-7.6 8.38 8.38 0 013.8-.9h.5a8.48 8.48 0 018 8v.5z"/>
            </svg>
          }
          @case ('google-workspace') {
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
              <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 01-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z" fill="#4285F4"/>
              <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
              <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05"/>
              <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
            </svg>
          }
          @case ('discord') {
            <svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
              <path d="M20.317 4.37a19.791 19.791 0 0 0-4.885-1.515.074.074 0 0 0-.079.037c-.21.375-.444.864-.608 1.25a18.27 18.27 0 0 0-5.487 0 12.64 12.64 0 0 0-.617-1.25.077.077 0 0 0-.079-.037A19.736 19.736 0 0 0 3.677 4.37a.07.07 0 0 0-.032.027C.533 9.046-.32 13.58.099 18.057a.082.082 0 0 0 .031.057 19.9 19.9 0 0 0 5.993 3.03.078.078 0 0 0 .084-.028 14.09 14.09 0 0 0 1.226-1.994.076.076 0 0 0-.041-.106 13.107 13.107 0 0 1-1.872-.892.077.077 0 0 1-.008-.128 10.2 10.2 0 0 0 .372-.292.074.074 0 0 1 .077-.01c3.928 1.793 8.18 1.793 12.062 0a.074.074 0 0 1 .078.01c.12.098.246.198.373.292a.077.077 0 0 1-.006.127 12.299 12.299 0 0 1-1.873.892.077.077 0 0 0-.041.107c.36.698.772 1.362 1.225 1.993a.076.076 0 0 0 .084.028 19.839 19.839 0 0 0 6.002-3.03.077.077 0 0 0 .032-.054c.5-5.177-.838-9.674-3.549-13.66a.061.061 0 0 0-.031-.03zM8.02 15.33c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.956-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.419 0 1.334-.956 2.419-2.157 2.419zm7.975 0c-1.183 0-2.157-1.085-2.157-2.419 0-1.333.955-2.419 2.157-2.419 1.21 0 2.176 1.096 2.157 2.419 0 1.334-.946 2.419-2.157 2.419z"/>
            </svg>
          }
          @case ('slack') {
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <rect x="7" y="3" width="3" height="8" rx="1.5"/>
              <rect x="3" y="7" width="8" height="3" rx="1.5"/>
              <rect x="14" y="13" width="3" height="8" rx="1.5"/>
              <rect x="13" y="14" width="8" height="3" rx="1.5"/>
            </svg>
          }
          @default {
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/></svg>
          }
        }
      </div>

      <span class="int-name">{{ displayName() }}</span>

      @if (status()?.connected) {
        <span class="int-value">{{ displayValue() }}</span>
        <span class="int-badge connected">Connected</span>
      } @else {
        <button class="int-connect-btn" [attr.data-channel]="channel()" (click)="connectClick($event)">
          @if (connecting()) {
            <span class="btn-spinner"></span>
          } @else {
            Connect
          }
        </button>
      }
    </div>
  `,
  styleUrls: ['./integration-card.component.scss'],
})
export class IntegrationCardComponent {
  channel = input.required<IntegrationChannelType>();
  status = input<IntegrationStatus | null>(null);
  connecting = input(false);
  skillName = input.required<string>();

  openDetail = output<void>();
  quickConnect = output<void>();

  displayName = computed(() => {
    const names: Record<IntegrationChannelType, string> = {
      email: 'Email',
      telegram: 'Telegram',
      whatsapp: 'WhatsApp',
      'google-workspace': 'Google Workspace',
      discord: 'Discord',
      slack: 'Slack',
    };
    return names[this.channel()] ?? this.skillName();
  });

  displayValue = computed(() => {
    const s = this.status();
    if (!s?.displayValue) return '';
    const ch = this.channel();
    if (ch === 'telegram') return `@${s.displayValue}`;
    return s.displayValue;
  });

  connectClick(e: Event): void {
    e.stopPropagation();
    this.quickConnect.emit();
  }
}
