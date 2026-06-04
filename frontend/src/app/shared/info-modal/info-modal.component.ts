import { Component, Input, signal, ChangeDetectionStrategy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { TranslateModule } from '@ngx-translate/core';

export interface LegendItem {
  color: string;
  labelKey: string;
  descKey: string;
}

export interface InfoModalConfig {
  titleKey: string;
  paragraphKeys: string[];
  icon: string;
  legend?: LegendItem[];
}

@Component({
  selector: 'app-info-modal',
  standalone: true,
  imports: [CommonModule, TranslateModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (visible()) {
      <div
        class="modal-dismiss-scrim modal-dismiss-scrim--system info-backdrop"
        [class.closing]="closing()"
        (click)="dismiss()"
      >
        <div
          class="modal-panel info-card"
          [class.closing]="closing()"
          (click)="$event.stopPropagation()"
        >
          <div class="modal-top-bar info-top-bar">
            <div class="icon-container">
              <div class="icon-glow"></div>
              <span class="icon-emoji">{{ config.icon }}</span>
            </div>
            <button type="button" class="modal-close" (click)="dismiss()" aria-label="Close">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
              </svg>
            </button>
          </div>

          <div class="modal-body card-content">
            <h2 class="title" [innerHTML]="config.titleKey | translate"></h2>

            @for (key of config.paragraphKeys; track key) {
              <p class="body-text" [innerHTML]="key | translate"></p>
            }

            @if (config.legend && config.legend.length) {
              <div class="legend">
                @for (item of config.legend; track item.labelKey) {
                  <div class="legend-item">
                    <span class="legend-dot" [style.background]="item.color" [style.box-shadow]="'0 0 6px ' + item.color + '80'"></span>
                    <span class="legend-label" [style.color]="item.color" [innerHTML]="item.labelKey | translate"></span>
                    <span class="legend-desc" [innerHTML]="item.descKey | translate"></span>
                  </div>
                }
              </div>
            }

            <button class="dismiss-btn" (click)="dismiss()">
              {{ 'common.got_it' | translate }}
            </button>
          </div>
        </div>
      </div>
    }
  `,
  styles: `
    .info-card {
      max-width: 540px;
      width: 100%;
      max-height: 85vh;
    }

    .info-top-bar {
      border-bottom: none;
      padding-bottom: 0;
    }

    .card-content {
      display: flex;
      flex-direction: column;
      align-items: center;
      text-align: center;
      padding-top: 0;
    }

    .icon-container {
      position: relative;
      margin-bottom: 0;
    }

    .icon-glow {
      position: absolute;
      inset: -12px;
      border-radius: 50%;
      background: radial-gradient(circle, var(--accent-primary-glow), transparent 70%);
    }

    .icon-emoji {
      position: relative;
      font-size: 2rem;
      line-height: 1;
    }

    .title {
      font-family: var(--font-sans);
      font-size: 1.25rem;
      font-weight: 600;
      color: var(--text-primary);
      margin-bottom: 16px;
      line-height: 1.3;
    }

    .body-text {
      font-family: var(--font-sans);
      font-size: 0.9rem;
      color: var(--text-secondary);
      line-height: 1.65;
      margin-bottom: 12px;
      text-align: left;
      width: 100%;

      :host ::ng-deep b {
        color: var(--accent-primary);
        font-weight: 500;
      }
    }

    .legend {
      width: 100%;
      display: flex;
      flex-direction: column;
      gap: 8px;
      margin: 8px 0 4px;
      padding: 14px 16px;
      border-radius: var(--radius-sm);
      background: var(--surface-inset);
      border: 1px solid var(--glass-border);
    }

    .legend-item {
      display: flex;
      align-items: center;
      gap: 10px;
      text-align: left;
    }

    .legend-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      flex-shrink: 0;
    }

    .legend-label {
      font-family: var(--font-sans);
      font-size: 0.825rem;
      font-weight: 600;
      flex-shrink: 0;
      min-width: 100px;
    }

    .legend-desc {
      font-family: var(--font-sans);
      font-size: 0.8rem;
      color: var(--text-muted);
      line-height: 1.4;
    }

    .dismiss-btn {
      margin-top: 16px;
      width: 100%;
      height: 42px;
      border: none;
      border-radius: var(--radius-sm);
      background: var(--accent-primary);
      color: #0c0d14;
      font-family: var(--font-sans);
      font-size: 0.875rem;
      font-weight: 600;
      cursor: pointer;
      transition: filter 200ms, box-shadow 200ms;
      letter-spacing: 0.01em;
      flex-shrink: 0;

      &:hover {
        filter: brightness(1.08);
        box-shadow: var(--shadow-glow);
      }

      &:active {
        filter: brightness(0.95);
      }
    }
  `,
})
export class InfoModalComponent {
  @Input({ required: true }) config!: InfoModalConfig;

  visible = signal(false);
  closing = signal(false);

  show() {
    this.closing.set(false);
    this.visible.set(true);
  }

  dismiss() {
    this.closing.set(true);
    setTimeout(() => this.visible.set(false), 150);
  }
}
