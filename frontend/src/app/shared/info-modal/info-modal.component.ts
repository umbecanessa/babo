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
        class="info-backdrop"
        [class.closing]="closing()"
        (click)="dismiss()"
      >
        <div
          class="info-card"
          [class.closing]="closing()"
          (click)="$event.stopPropagation()"
        >
          <div class="accent-bar"></div>

          <div class="card-content">
            <div class="icon-container">
              <div class="icon-glow"></div>
              <span class="icon-emoji">{{ config.icon }}</span>
            </div>

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
    .info-backdrop {
      position: fixed;
      inset: 0;
      z-index: 10000;
      display: flex;
      align-items: center;
      justify-content: center;
      background: var(--backdrop-scrim);
      animation: backdropIn 200ms ease-out forwards;
      padding: 24px;

      &.closing {
        animation: backdropOut 150ms ease-in forwards;
      }
    }

    .info-card {
      position: relative;
      max-width: 540px;
      width: 100%;
      max-height: 85vh;
      border-radius: 16px;
      background: rgba(15, 15, 25, 0.92);
      backdrop-filter: blur(24px);
      -webkit-backdrop-filter: blur(24px);
      border: 1px solid var(--accent-primary-glow);
      overflow: hidden;
      animation: cardIn 250ms ease-out forwards;
      box-shadow:
        0 0 80px var(--accent-primary-glow),
        0 25px 50px rgba(0, 0, 0, 0.4);
      display: flex;
      flex-direction: column;

      &.closing {
        animation: cardOut 150ms ease-in forwards;
      }
    }

    .accent-bar {
      height: 3px;
      flex-shrink: 0;
      background: linear-gradient(90deg, var(--accent), var(--accent-purple));
    }

    .card-content {
      padding: 32px 28px 24px;
      display: flex;
      flex-direction: column;
      align-items: center;
      text-align: center;
      overflow-y: auto;
    }

    .icon-container {
      position: relative;
      margin-bottom: 20px;
    }

    .icon-glow {
      position: absolute;
      inset: -12px;
      border-radius: 50%;
      background: radial-gradient(circle, var(--accent-primary-glow), transparent 70%);
    }

    .icon-emoji {
      position: relative;
      font-size: 2.5rem;
      line-height: 1;
    }

    .title {
      font-family: 'Inter', sans-serif;
      font-size: 1.25rem;
      font-weight: 600;
      color: var(--text-primary);
      margin-bottom: 16px;
      line-height: 1.3;
    }

    .body-text {
      font-family: 'Inter', sans-serif;
      font-size: 0.9rem;
      color: #94a3b8;
      line-height: 1.65;
      margin-bottom: 12px;
      text-align: left;
      width: 100%;

      :host ::ng-deep b {
        color: var(--accent);
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
      border-radius: 10px;
      background: var(--overlay-1);
      border: 1px solid var(--overlay-2);
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
      font-family: 'Inter', sans-serif;
      font-size: 0.825rem;
      font-weight: 600;
      flex-shrink: 0;
      min-width: 100px;
    }

    .legend-desc {
      font-family: 'Inter', sans-serif;
      font-size: 0.8rem;
      color: #8896ab;
      line-height: 1.4;
    }

    .dismiss-btn {
      margin-top: 16px;
      width: 100%;
      height: 42px;
      border: none;
      border-radius: 10px;
      background: linear-gradient(135deg, var(--accent), var(--accent-purple));
      color: #0f0f19;
      font-family: 'Inter', sans-serif;
      font-size: 0.875rem;
      font-weight: 600;
      cursor: pointer;
      transition: filter 200ms, box-shadow 200ms;
      letter-spacing: 0.01em;
      flex-shrink: 0;

      &:hover {
        filter: brightness(1.1);
        box-shadow: 0 0 20px var(--accent-primary-glow);
      }

      &:active {
        filter: brightness(0.95);
      }
    }

    @keyframes backdropIn {
      from { opacity: 0; }
      to { opacity: 1; }
    }
    @keyframes backdropOut {
      from { opacity: 1; }
      to { opacity: 0; }
    }
    @keyframes cardIn {
      from { opacity: 0; transform: scale(0.95); }
      to { opacity: 1; transform: scale(1); }
    }
    @keyframes cardOut {
      from { opacity: 1; transform: scale(1); }
      to { opacity: 0; transform: scale(0.95); }
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
