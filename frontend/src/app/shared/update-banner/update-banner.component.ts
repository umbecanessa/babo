import { Component, computed, signal, inject, ChangeDetectionStrategy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { UpdateService } from '../../core/services/update.service';

@Component({
  selector: 'app-update-banner',
  standalone: true,
  imports: [CommonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="update-trigger" (click)="togglePanel()">
      <svg class="update-icon" viewBox="0 0 20 20" fill="currentColor" width="18" height="18">
        <path fill-rule="evenodd" d="M10 3a.75.75 0 01.75.75v10.638l3.96-4.158a.75.75 0 111.08 1.04l-5.25 5.5a.75.75 0 01-1.08 0l-5.25-5.5a.75.75 0 111.08-1.04l3.96 4.158V3.75A.75.75 0 0110 3z" clip-rule="evenodd"/>
      </svg>
      <span class="badge-dot" [class.downloading]="state() === 'downloading'" [class.ready]="state() === 'downloaded'"></span>
    </div>

    @if (panelOpen()) {
      <div class="panel-backdrop" (click)="togglePanel()"></div>
      <div class="panel" (click)="$event.stopPropagation()">
        <div class="panel-accent"></div>

        <!-- Available state -->
        @if (state() === 'available') {
          <div class="panel-body">
            <div class="panel-header">
              <span class="panel-title">Update Available</span>
              <span class="panel-version">v{{ info()?.version }}</span>
            </div>
            @if (info()?.releaseNotes) {
              <p class="release-notes">{{ info()?.releaseNotes }}</p>
            }
            <div class="panel-actions">
              <button class="btn-primary" (click)="onDownload()">Download</button>
              <div class="snooze-group">
                <button class="btn-ghost" (click)="onSnooze(3600000)">1h</button>
                <button class="btn-ghost" (click)="onSnooze(14400000)">4h</button>
                <button class="btn-ghost" (click)="onSnooze(86400000)">1d</button>
              </div>
            </div>
          </div>
        }

        <!-- Downloading state -->
        @if (state() === 'downloading') {
          <div class="panel-body">
            <div class="panel-header">
              <span class="panel-title">Downloading v{{ info()?.version }}</span>
              <span class="panel-pct">{{ progressPct() }}%</span>
            </div>
            <div class="progress-track">
              <div class="progress-fill" [style.width.%]="progressPct()"></div>
            </div>
            <div class="progress-meta">
              <span>{{ transferredMB() }} / {{ totalMB() }} MB</span>
              <span>{{ speedMBs() }} MB/s</span>
            </div>
          </div>
        }

        <!-- Downloaded / ready state -->
        @if (state() === 'downloaded') {
          <div class="panel-body">
            <div class="panel-header">
              <span class="panel-title">Ready to Install</span>
              <span class="panel-version">v{{ info()?.version }}</span>
            </div>
            <p class="release-notes">The update has been downloaded. Install and restart to apply.</p>
            <div class="panel-actions">
              <button class="btn-primary" (click)="onInstall()">Install & Restart</button>
            </div>
          </div>
        }

        <!-- Error state -->
        @if (state() === 'error') {
          <div class="panel-body">
            <div class="panel-header">
              <span class="panel-title error-title">Update Failed</span>
            </div>
            <p class="release-notes error-text">{{ updateService.errorMessage() }}</p>
            <div class="panel-actions">
              <button class="btn-primary" (click)="onRetry()">Retry</button>
            </div>
          </div>
        }
      </div>
    }
  `,
  styles: `
    :host {
      position: relative;
      display: flex;
      align-items: center;
    }

    .update-trigger {
      position: relative;
      display: flex;
      align-items: center;
      justify-content: center;
      width: 32px;
      height: 32px;
      border-radius: 8px;
      cursor: pointer;
      transition: background 150ms;
      color: var(--accent-primary);
      -webkit-app-region: no-drag;

      &:hover {
        background: var(--overlay-3);
      }
    }

    .update-icon {
      transition: transform 200ms;
    }

    .badge-dot {
      position: absolute;
      top: 4px;
      right: 4px;
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--accent-primary);
      animation: pulse-dot 2s ease-in-out infinite;

      &.downloading {
        background: var(--accent-amber, var(--accent-warn));
        animation: pulse-dot 1s ease-in-out infinite;
      }
      &.ready {
        background: var(--accent-green, var(--accent-success));
        animation: pulse-dot 1.5s ease-in-out infinite;
      }
    }

    @keyframes pulse-dot {
      0%, 100% { opacity: 1; transform: scale(1); }
      50% { opacity: 0.5; transform: scale(1.3); }
    }

    .panel-backdrop {
      position: fixed;
      inset: 0;
      z-index: 9998;
    }

    .panel {
      position: absolute;
      top: calc(100% + 8px);
      right: 0;
      z-index: 9999;
      width: 340px;
      border-radius: 12px;
      background: rgba(15, 15, 25, 0.95);
      backdrop-filter: blur(24px);
      -webkit-backdrop-filter: blur(24px);
      border: 1px solid var(--accent-primary-glow);
      box-shadow: 0 16px 48px var(--overlay-5), 0 0 40px var(--accent-primary-glow);
      overflow: hidden;
      animation: panelIn 150ms ease-out;
    }

    @keyframes panelIn {
      from { opacity: 0; transform: translateY(-6px); }
      to { opacity: 1; transform: translateY(0); }
    }

    .panel-accent {
      height: 2px;
      background: linear-gradient(90deg, var(--accent-primary), var(--accent-primary));
    }

    .panel-body {
      padding: 16px;
    }

    .panel-header {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      margin-bottom: 8px;
    }

    .panel-title {
      font-family: 'Inter', sans-serif;
      font-size: 0.875rem;
      font-weight: 600;
      color: var(--text-primary);
    }

    .panel-version {
      font-family: 'JetBrains Mono', monospace;
      font-size: 0.75rem;
      color: var(--accent-primary);
      background: var(--accent-primary-glow);
      padding: 2px 8px;
      border-radius: 6px;
    }

    .panel-pct {
      font-family: 'JetBrains Mono', monospace;
      font-size: 0.8rem;
      font-weight: 600;
      color: var(--accent-amber, var(--accent-warn));
    }

    .release-notes {
      font-family: 'Inter', sans-serif;
      font-size: 0.8rem;
      color: #94a3b8;
      line-height: 1.5;
      margin-bottom: 12px;
      max-height: 80px;
      overflow-y: auto;
    }

    .error-title { color: var(--accent-red, var(--accent-danger)); }
    .error-text { color: var(--accent-red, var(--accent-danger)); opacity: 0.85; }

    .progress-track {
      width: 100%;
      height: 6px;
      border-radius: 3px;
      background: var(--overlay-3);
      overflow: hidden;
      margin-bottom: 8px;
    }

    .progress-fill {
      height: 100%;
      border-radius: 3px;
      background: linear-gradient(90deg, var(--accent-primary), var(--accent-primary));
      transition: width 300ms ease-out;
    }

    .progress-meta {
      display: flex;
      justify-content: space-between;
      font-family: 'JetBrains Mono', monospace;
      font-size: 0.7rem;
      color: #64748b;
    }

    .panel-actions {
      display: flex;
      align-items: center;
      gap: 8px;
    }

    .btn-primary {
      flex: 1;
      height: 36px;
      border: none;
      border-radius: 8px;
      background: linear-gradient(135deg, var(--accent-primary), var(--accent-primary));
      color: #0f0f19;
      font-family: 'Inter', sans-serif;
      font-size: 0.8rem;
      font-weight: 600;
      cursor: pointer;
      transition: filter 150ms, box-shadow 150ms;

      &:hover {
        filter: brightness(1.1);
        box-shadow: 0 0 16px var(--accent-primary-glow);
      }
    }

    .snooze-group {
      display: flex;
      gap: 4px;
    }

    .btn-ghost {
      height: 36px;
      padding: 0 10px;
      border: 1px solid var(--overlay-3);
      border-radius: 8px;
      background: transparent;
      color: #94a3b8;
      font-family: 'Inter', sans-serif;
      font-size: 0.75rem;
      font-weight: 500;
      cursor: pointer;
      transition: border-color 150ms, color 150ms;

      &:hover {
        border-color: var(--overlay-5);
        color: var(--text-secondary);
      }
    }
  `,
})
export class UpdateBannerComponent {
  readonly updateService = inject(UpdateService);
  readonly panelOpen = signal(false);

  readonly state = this.updateService.state;
  readonly info = this.updateService.updateInfo;

  readonly progressPct = computed(() => Math.round(this.updateService.progress()?.percent ?? 0));
  readonly transferredMB = computed(() => ((this.updateService.progress()?.transferred ?? 0) / 1048576).toFixed(1));
  readonly totalMB = computed(() => ((this.updateService.progress()?.total ?? 0) / 1048576).toFixed(1));
  readonly speedMBs = computed(() => ((this.updateService.progress()?.bytesPerSecond ?? 0) / 1048576).toFixed(1));

  togglePanel() {
    this.panelOpen.update(v => !v);
  }

  onDownload() {
    this.panelOpen.set(true);
    this.updateService.download();
  }

  onInstall() {
    this.updateService.install();
  }

  onSnooze(ms: number) {
    this.updateService.snooze(ms);
    this.panelOpen.set(false);
  }

  onRetry() {
    this.updateService.check();
  }
}
