import { Component, signal, ChangeDetectionStrategy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { UpdateService } from '../../core/services/update.service';

@Component({
  selector: 'app-update-modal',
  standalone: true,
  imports: [CommonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (updateService.showModal()) {
      <div class="modal-backdrop" [class.closing]="closing()" (click)="onLater()">
        <div class="modal-card" [class.closing]="closing()" (click)="$event.stopPropagation()">
          <div class="accent-bar"></div>

          <div class="card-body">
            <div class="icon-container">
              <div class="icon-glow"></div>
              <svg class="icon-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="32" height="32">
                <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/>
                <polyline points="7 10 12 15 17 10"/>
                <line x1="12" y1="15" x2="12" y2="3"/>
              </svg>
            </div>

            <h2 class="title">Time to Update</h2>
            <p class="version">Version {{ updateService.updateInfo()?.version }} is available</p>

            @if (updateService.updateInfo()?.releaseNotes) {
              <p class="notes">{{ updateService.updateInfo()?.releaseNotes }}</p>
            }

            <div class="actions">
              <button class="btn-primary" (click)="onUpdate()">Update Now</button>
              <button class="btn-secondary" (click)="onLater()">Later</button>
            </div>
          </div>
        </div>
      </div>
    }
  `,
  styles: `
    .modal-backdrop {
      position: fixed;
      inset: 0;
      z-index: 10000;
      display: flex;
      align-items: center;
      justify-content: center;
      background: rgba(0, 0, 0, 0.6);
      animation: backdropIn 200ms ease-out forwards;
      padding: 24px;

      &.closing { animation: backdropOut 150ms ease-in forwards; }
    }

    .modal-card {
      position: relative;
      max-width: 420px;
      width: 100%;
      border-radius: 16px;
      background: rgba(15, 15, 25, 0.92);
      backdrop-filter: blur(24px);
      -webkit-backdrop-filter: blur(24px);
      border: 1px solid rgba(56, 189, 248, 0.12);
      overflow: hidden;
      animation: cardIn 250ms ease-out forwards;
      box-shadow:
        0 0 80px rgba(56, 189, 248, 0.06),
        0 25px 50px rgba(0, 0, 0, 0.4);

      &.closing { animation: cardOut 150ms ease-in forwards; }
    }

    .accent-bar {
      height: 3px;
      background: linear-gradient(90deg, var(--accent, #38bdf8), var(--accent-purple, #a78bfa));
    }

    .card-body {
      padding: 32px 28px 24px;
      display: flex;
      flex-direction: column;
      align-items: center;
      text-align: center;
    }

    .icon-container {
      position: relative;
      margin-bottom: 20px;
      color: var(--accent, #38bdf8);
    }

    .icon-glow {
      position: absolute;
      inset: -16px;
      border-radius: 50%;
      background: radial-gradient(circle, rgba(56, 189, 248, 0.15), transparent 70%);
    }

    .icon-svg { position: relative; }

    .title {
      font-family: 'Inter', sans-serif;
      font-size: 1.25rem;
      font-weight: 600;
      color: #f1f5f9;
      margin-bottom: 8px;
    }

    .version {
      font-family: 'JetBrains Mono', monospace;
      font-size: 0.85rem;
      color: var(--accent, #38bdf8);
      margin-bottom: 12px;
    }

    .notes {
      font-family: 'Inter', sans-serif;
      font-size: 0.85rem;
      color: #94a3b8;
      line-height: 1.6;
      margin-bottom: 20px;
      max-height: 120px;
      overflow-y: auto;
      text-align: left;
      width: 100%;
      padding: 12px;
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.02);
      border: 1px solid rgba(255, 255, 255, 0.04);
    }

    .actions {
      display: flex;
      gap: 10px;
      width: 100%;
    }

    .btn-primary {
      flex: 1;
      height: 42px;
      border: none;
      border-radius: 10px;
      background: linear-gradient(135deg, var(--accent, #38bdf8), var(--accent-purple, #a78bfa));
      color: #0f0f19;
      font-family: 'Inter', sans-serif;
      font-size: 0.875rem;
      font-weight: 600;
      cursor: pointer;
      transition: filter 200ms, box-shadow 200ms;

      &:hover {
        filter: brightness(1.1);
        box-shadow: 0 0 20px rgba(56, 189, 248, 0.3);
      }
    }

    .btn-secondary {
      flex: 1;
      height: 42px;
      border: 1px solid rgba(255, 255, 255, 0.1);
      border-radius: 10px;
      background: transparent;
      color: #94a3b8;
      font-family: 'Inter', sans-serif;
      font-size: 0.875rem;
      font-weight: 500;
      cursor: pointer;
      transition: border-color 200ms, color 200ms;

      &:hover {
        border-color: rgba(255, 255, 255, 0.2);
        color: #e2e8f0;
      }
    }

    @keyframes backdropIn { from { opacity: 0; } to { opacity: 1; } }
    @keyframes backdropOut { from { opacity: 1; } to { opacity: 0; } }
    @keyframes cardIn { from { opacity: 0; transform: scale(0.95); } to { opacity: 1; transform: scale(1); } }
    @keyframes cardOut { from { opacity: 1; transform: scale(1); } to { opacity: 0; transform: scale(0.95); } }
  `,
})
export class UpdateModalComponent {
  closing = signal(false);

  constructor(public updateService: UpdateService) {}

  onUpdate() {
    this.dismiss();
    this.updateService.download();
  }

  onLater() {
    this.dismiss();
    this.updateService.dismissModal();
  }

  private dismiss() {
    this.closing.set(true);
    setTimeout(() => this.closing.set(false), 150);
  }
}
