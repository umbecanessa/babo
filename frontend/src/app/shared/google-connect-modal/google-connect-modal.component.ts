import {
  Component,
  input,
  output,
  signal,
  OnChanges,
  SimpleChanges,
  OnDestroy,
  HostListener,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { ApiService } from '../../core/services/api.service';
import { PlatformService } from '../../core/services/platform.service';

type ConnectState = 'idle' | 'connecting' | 'waiting' | 'connected' | 'error';

@Component({
  selector: 'app-google-connect-modal',
  standalone: true,
  imports: [CommonModule],
  template: `
    @if (open()) {
      <div class="modal-backdrop" [class.closing]="closing" (click)="dismiss()">
        <div class="modal-panel" (click)="$event.stopPropagation()">
          <div class="modal-top-bar">
            <div class="modal-header-left">
              <svg viewBox="0 0 24 24" fill="currentColor" width="22" height="22" class="google-icon">
                <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 01-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z" fill="#4285F4"/>
                <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
                <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05"/>
                <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
              </svg>
              <h2 class="modal-title">Google Workspace</h2>
            </div>
            <button class="modal-close" (click)="dismiss()" aria-label="Close">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
              </svg>
            </button>
          </div>

          <div class="modal-body">
            @switch (state()) {
              @case ('idle') {
                <div class="state-idle">
                  <p class="description">
                    Connect your Google account to give your agent access to Gmail, Calendar, Drive, and Sheets.
                  </p>
                  <p class="description muted">
                    A browser window will open for you to sign in and authorize access. Your credentials stay on your device.
                  </p>
                  <button class="btn-connect" (click)="startConnect()">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                      <path d="M15 3h4a2 2 0 012 2v14a2 2 0 01-2 2h-4"/><polyline points="10 17 15 12 10 7"/><line x1="15" y1="12" x2="3" y2="12"/>
                    </svg>
                    Connect Google Account
                  </button>
                </div>
              }

              @case ('connecting') {
                <div class="state-loading">
                  <div class="spinner"></div>
                  <p>Preparing authorization...</p>
                </div>
              }

              @case ('waiting') {
                <div class="state-waiting">
                  <div class="pulse-ring"></div>
                  <p class="waiting-text">Waiting for authorization...</p>
                  <p class="waiting-hint">
                    A browser window has been opened. Sign in to your Google account and grant access.
                    This dialog will update automatically.
                  </p>
                  <button class="btn-secondary" (click)="startConnect()">
                    Re-open browser
                  </button>
                </div>
              }

              @case ('connected') {
                <div class="state-connected">
                  <div class="success-icon">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" width="32" height="32">
                      <polyline points="20 6 9 17 4 12"/>
                    </svg>
                  </div>
                  <p class="connected-label">Connected</p>
                  @if (connectedEmail()) {
                    <p class="connected-email">{{ connectedEmail() }}</p>
                  }
                  <div class="connected-actions">
                    <button class="btn-done" (click)="dismiss()">Done</button>
                    <button class="btn-disconnect" (click)="startDisconnect()">Disconnect</button>
                  </div>
                </div>
              }

              @case ('error') {
                <div class="state-error">
                  <div class="error-icon">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="32" height="32">
                      <circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/>
                    </svg>
                  </div>
                  <p class="error-text">{{ errorMessage() }}</p>
                  <button class="btn-connect" (click)="startConnect()">Try Again</button>
                </div>
              }
            }
          </div>
        </div>
      </div>
    }
  `,
  styles: [`
    $bg-panel: #0d0d12;
    $border: rgba(255, 255, 255, 0.08);
    $accent: #38bdf8;
    $green: #4ade80;
    $red: #f87171;
    $text-heading: #e5e5e5;
    $text-muted: #787890;

    .modal-backdrop {
      position: fixed;
      inset: 0;
      z-index: 1000;
      display: flex;
      align-items: center;
      justify-content: center;
      background: rgba(0, 0, 0, 0.65);
      backdrop-filter: blur(6px);
      animation: fadeIn 0.18s ease-out;

      &.closing { animation: fadeOut 0.18s ease-in forwards; }
    }

    .modal-panel {
      width: 92vw;
      max-width: 480px;
      display: flex;
      flex-direction: column;
      background: $bg-panel;
      border: 1px solid $border;
      border-radius: 14px;
      box-shadow: 0 24px 80px rgba(0, 0, 0, 0.6);
      animation: slideUp 0.22s ease-out;

      .closing & { animation: slideDown 0.18s ease-in forwards; }
    }

    .modal-top-bar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 18px 24px;
      border-bottom: 1px solid $border;
    }

    .modal-header-left {
      display: flex;
      align-items: center;
      gap: 10px;
    }

    .modal-title {
      font-size: 18px;
      font-weight: 600;
      color: $text-heading;
      margin: 0;
    }

    .modal-close {
      display: flex;
      align-items: center;
      justify-content: center;
      width: 32px;
      height: 32px;
      background: transparent;
      border: 1px solid $border;
      border-radius: 8px;
      color: $text-muted;
      cursor: pointer;
      transition: all 0.15s;

      &:hover {
        color: $text-heading;
        border-color: rgba(255, 255, 255, 0.15);
        background: rgba(255, 255, 255, 0.04);
      }
    }

    .modal-body {
      padding: 28px 24px;
      text-align: center;
    }

    .description {
      color: $text-heading;
      font-size: 14px;
      line-height: 1.6;
      margin: 0 0 12px;

      &.muted { color: $text-muted; font-size: 13px; }
    }

    .btn-connect {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      margin-top: 16px;
      padding: 10px 24px;
      background: $accent;
      color: #000;
      font-weight: 600;
      font-size: 14px;
      border: none;
      border-radius: 8px;
      cursor: pointer;
      transition: all 0.15s;

      &:hover { filter: brightness(1.1); transform: translateY(-1px); }
      &:active { transform: translateY(0); }
    }

    .btn-secondary {
      margin-top: 16px;
      padding: 8px 20px;
      background: transparent;
      color: $text-muted;
      font-size: 13px;
      border: 1px solid $border;
      border-radius: 8px;
      cursor: pointer;
      transition: all 0.15s;

      &:hover { color: $text-heading; border-color: rgba(255, 255, 255, 0.15); }
    }

    .btn-done {
      padding: 10px 32px;
      background: $green;
      color: #000;
      font-weight: 600;
      font-size: 14px;
      border: none;
      border-radius: 8px;
      cursor: pointer;
      transition: all 0.15s;

      &:hover { filter: brightness(1.1); }
    }

    .btn-disconnect {
      padding: 8px 20px;
      background: transparent;
      color: $text-muted;
      font-size: 13px;
      border: 1px solid $border;
      border-radius: 8px;
      cursor: pointer;
      transition: all 0.15s;

      &:hover { color: $red; border-color: rgba(248, 113, 113, 0.3); }
    }

    /* --- States --- */

    .state-idle, .state-loading, .state-waiting, .state-connected, .state-error {
      display: flex;
      flex-direction: column;
      align-items: center;
    }

    .spinner {
      width: 32px;
      height: 32px;
      border: 3px solid $border;
      border-top-color: $accent;
      border-radius: 50%;
      animation: spin 0.8s linear infinite;
      margin-bottom: 16px;
    }

    .pulse-ring {
      width: 48px;
      height: 48px;
      border-radius: 50%;
      border: 3px solid $accent;
      animation: pulse 1.6s ease-in-out infinite;
      margin-bottom: 20px;
    }

    .waiting-text {
      color: $text-heading;
      font-size: 15px;
      font-weight: 500;
      margin: 0 0 8px;
    }

    .waiting-hint {
      color: $text-muted;
      font-size: 13px;
      line-height: 1.5;
      margin: 0;
      max-width: 360px;
    }

    .success-icon {
      width: 56px;
      height: 56px;
      border-radius: 50%;
      background: rgba(74, 222, 128, 0.12);
      display: flex;
      align-items: center;
      justify-content: center;
      color: $green;
      margin-bottom: 16px;
    }

    .connected-label {
      color: $green;
      font-size: 16px;
      font-weight: 600;
      margin: 0 0 4px;
    }

    .connected-email {
      color: $text-muted;
      font-size: 14px;
      margin: 0 0 20px;
    }

    .connected-actions {
      display: flex;
      gap: 12px;
      align-items: center;
    }

    .error-icon {
      width: 56px;
      height: 56px;
      border-radius: 50%;
      background: rgba(248, 113, 113, 0.12);
      display: flex;
      align-items: center;
      justify-content: center;
      color: $red;
      margin-bottom: 16px;
    }

    .error-text {
      color: $text-heading;
      font-size: 14px;
      margin: 0;
    }

    @keyframes fadeIn  { from { opacity: 0; } to { opacity: 1; } }
    @keyframes fadeOut { from { opacity: 1; } to { opacity: 0; } }
    @keyframes slideUp   { from { opacity: 0; transform: translateY(24px) scale(0.97); } to { opacity: 1; transform: translateY(0) scale(1); } }
    @keyframes slideDown { from { opacity: 1; transform: translateY(0) scale(1); } to { opacity: 0; transform: translateY(16px) scale(0.97); } }
    @keyframes spin  { to { transform: rotate(360deg); } }
    @keyframes pulse { 0%, 100% { opacity: 1; transform: scale(1); } 50% { opacity: 0.4; transform: scale(1.15); } }
  `],
})
export class GoogleConnectModalComponent implements OnChanges, OnDestroy {
  open = input(false);
  agentId = input('');
  closed = output<void>();
  connected = output<{ email: string }>();

  state = signal<ConnectState>('idle');
  connectedEmail = signal('');
  errorMessage = signal('');
  closing = false;

  private pollTimer: ReturnType<typeof setInterval> | null = null;

  @HostListener('document:keydown.escape')
  onEsc(): void {
    if (this.open()) this.dismiss();
  }

  constructor(
    private api: ApiService,
    private platform: PlatformService,
  ) {}

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['open'] && this.open()) {
      this.checkInitialStatus();
    }
    if (changes['open'] && !this.open()) {
      this.stopPolling();
    }
  }

  ngOnDestroy(): void {
    this.stopPolling();
  }

  dismiss(): void {
    this.closing = true;
    this.stopPolling();
    setTimeout(() => {
      this.closing = false;
      this.closed.emit();
    }, 180);
  }

  startConnect(): void {
    const agentId = this.agentId();
    if (!agentId) return;

    this.state.set('connecting');
    this.errorMessage.set('');

    this.api.connectGoogleWorkspace(agentId).subscribe({
      next: (res) => {
        if (res.connected) {
          this.connectedEmail.set(res.email || '');
          this.state.set('connected');
          this.connected.emit({ email: res.email || '' });
          return;
        }
        if (res.auth_url) {
          this.openBrowser(res.auth_url);
          this.state.set('waiting');
          this.startPolling();
        }
      },
      error: (err) => {
        this.errorMessage.set(err.error?.detail || err.message || 'Failed to start connection');
        this.state.set('error');
      },
    });
  }

  startDisconnect(): void {
    const agentId = this.agentId();
    if (!agentId) return;

    this.api.disconnectGoogleWorkspace(agentId).subscribe({
      next: () => {
        this.connectedEmail.set('');
        this.state.set('idle');
      },
      error: () => {
        this.connectedEmail.set('');
        this.state.set('idle');
      },
    });
  }

  private checkInitialStatus(): void {
    const agentId = this.agentId();
    if (!agentId) return;

    this.api.getGoogleWorkspaceStatus(agentId).subscribe({
      next: (status) => {
        if (status.connected) {
          this.connectedEmail.set(status.email || '');
          this.state.set('connected');
        } else {
          this.state.set('idle');
        }
      },
      error: () => this.state.set('idle'),
    });
  }

  private openBrowser(url: string): void {
    if (this.platform.isElectron) {
      const nls = (window as any).nls;
      if (nls?.openExternal) {
        nls.openExternal(url);
        return;
      }
    }
    window.open(url, '_blank');
  }

  private startPolling(): void {
    this.stopPolling();
    const agentId = this.agentId();
    if (!agentId) return;

    this.pollTimer = setInterval(() => {
      this.api.getGoogleWorkspaceStatus(agentId).subscribe({
        next: (status) => {
          if (status.connected) {
            this.connectedEmail.set(status.email || '');
            this.state.set('connected');
            this.connected.emit({ email: status.email || '' });
            this.stopPolling();
          }
        },
      });
    }, 2500);
  }

  private stopPolling(): void {
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
  }
}
