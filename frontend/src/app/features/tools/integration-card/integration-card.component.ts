import {
  Component,
  input,
  output,
  computed,
} from '@angular/core';
import { CommonModule } from '@angular/common';

export type IntegrationChannelType = 'email' | 'telegram' | 'whatsapp' | 'google-workspace';

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
