import { Component, Input, OnInit, signal, ChangeDetectionStrategy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { TranslateModule } from '@ngx-translate/core';
import { OnboardingService } from './onboarding.service';
import { OnboardingPageConfig } from './onboarding-content';

@Component({
  selector: 'app-onboarding-modal',
  standalone: true,
  imports: [CommonModule, TranslateModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (visible()) {
      <div
        class="onboarding-backdrop"
        [class.closing]="closing()"
        (click)="dismiss()"
      >
        <div
          class="onboarding-card"
          [class.closing]="closing()"
          (click)="$event.stopPropagation()"
        >
          <div class="accent-bar"></div>

          <div class="card-content">
            <div class="icon-container">
              <div class="icon-glow"></div>
              @if (isEmoji) {
                <span class="icon-emoji">{{ config.icon }}</span>
              } @else {
                <svg [innerHTML]="iconSvg" class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"></svg>
              }
            </div>

            <h2 class="title" [innerHTML]="'onboarding.' + config.pageKey + '.title' | translate"></h2>

            @for (i of paragraphIndices; track i) {
              <p class="body-text" [innerHTML]="'onboarding.' + config.pageKey + '.p' + i | translate"></p>
            }

            <button class="got-it-btn" (click)="dismiss()">
              {{ (config.buttonKey || 'onboarding_btn.got_it') | translate }}
            </button>
          </div>
        </div>
      </div>
    }
  `,
  styles: `
    .onboarding-backdrop {
      position: fixed;
      inset: 0;
      z-index: 10000;
      display: flex;
      align-items: center;
      justify-content: center;
      background: rgba(0, 0, 0, 0.6);
      animation: backdropIn 200ms ease-out forwards;
      padding: 24px;

      &.closing {
        animation: backdropOut 150ms ease-in forwards;
      }
    }

    .onboarding-card {
      position: relative;
      max-width: 480px;
      width: 100%;
      border-radius: 16px;
      background: rgba(15, 15, 25, 0.88);
      backdrop-filter: blur(24px);
      -webkit-backdrop-filter: blur(24px);
      border: 1px solid rgba(56, 189, 248, 0.12);
      overflow: hidden;
      animation: cardIn 250ms ease-out forwards;
      box-shadow:
        0 0 80px rgba(56, 189, 248, 0.06),
        0 25px 50px rgba(0, 0, 0, 0.4);

      &.closing {
        animation: cardOut 150ms ease-in forwards;
      }
    }

    .accent-bar {
      height: 3px;
      background: linear-gradient(90deg, var(--accent), var(--accent-purple));
    }

    .card-content {
      padding: 32px 28px 24px;
      display: flex;
      flex-direction: column;
      align-items: center;
      text-align: center;
    }

    .icon-container {
      position: relative;
      margin-bottom: 20px;
    }

    .icon-glow {
      position: absolute;
      inset: -12px;
      border-radius: 50%;
      background: radial-gradient(circle, rgba(56, 189, 248, 0.15), transparent 70%);
    }

    .icon {
      position: relative;
      width: 40px;
      height: 40px;
      color: var(--accent);
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
      color: #f1f5f9;
      margin-bottom: 16px;
      line-height: 1.3;
    }

    .body-text {
      font-family: 'Inter', sans-serif;
      font-size: 0.9rem;
      color: #94a3b8;
      line-height: 1.65;
      margin-bottom: 12px;

      :host ::ng-deep b {
        color: var(--accent);
        font-weight: 500;
      }
    }

    .got-it-btn {
      margin-top: 12px;
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

      &:hover {
        filter: brightness(1.1);
        box-shadow: 0 0 20px rgba(56, 189, 248, 0.3);
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
export class OnboardingModalComponent implements OnInit {
  @Input({ required: true }) config!: OnboardingPageConfig;

  visible = signal(false);
  closing = signal(false);
  paragraphIndices: number[] = [];
  iconSvg = '';
  isEmoji = false;

  private static readonly SVG_ICONS: Record<string, string> = {
    orb: '<circle cx="12" cy="12" r="8"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2"/>',
    plus: '<path d="M12 5v14m-7-7h14"/>',
    chat: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
    brain:
      '<path d="M12 2a7 7 0 0 1 7 7c0 2.5-1.3 4.7-3.2 6H8.2C6.3 13.7 5 11.5 5 9a7 7 0 0 1 7-7z"/><path d="M9 22h6m-5-4h4"/>',
    wrench:
      '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>',
  };

  constructor(private onboarding: OnboardingService) {}

  ngOnInit() {
    if (this.onboarding.hasSeen(this.config.pageKey)) return;
    this.initModal();
    this.visible.set(true);
  }

  private initModal() {
    this.paragraphIndices = Array.from({ length: this.config.paragraphCount }, (_, i) => i + 1);
    this.isEmoji = !(this.config.icon in OnboardingModalComponent.SVG_ICONS);
    if (!this.isEmoji) {
      this.iconSvg = OnboardingModalComponent.SVG_ICONS[this.config.icon] ?? '';
    }
  }

  dismiss() {
    this.closing.set(true);
    this.onboarding.markSeen(this.config.pageKey);
    setTimeout(() => this.visible.set(false), 150);
  }

  /** Re-show the modal (called by info button on host pages) */
  show() {
    this.onboarding.reset(this.config.pageKey);
    this.closing.set(false);
    this.initModal();
    this.visible.set(true);
  }
}
