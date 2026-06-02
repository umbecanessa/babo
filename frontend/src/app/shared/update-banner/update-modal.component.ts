import { Component, computed, inject, signal, ChangeDetectionStrategy, SecurityContext } from '@angular/core';
import { CommonModule } from '@angular/common';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import { UpdateService } from '../../core/services/update.service';
import { normalizeUpdateReleaseNotes } from './update-release-notes.util';

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
            <div class="icon-wrap">
              <svg class="icon-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="28" height="28" aria-hidden="true">
                <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/>
                <polyline points="7 10 12 15 17 10"/>
                <line x1="12" y1="15" x2="12" y2="3"/>
              </svg>
            </div>

            <h2 class="title">Update available</h2>
            <p class="version">
              <span class="version-label">Version</span>
              <span class="version-value">v{{ updateService.updateInfo()?.version }}</span>
            </p>

            @if (releaseNotesHtml()) {
              <div class="notes" [innerHTML]="releaseNotesHtml()"></div>
            } @else {
              <p class="notes-fallback">A new build is ready to install.</p>
            }

            <div class="actions">
              <button type="button" class="btn-primary" (click)="onUpdate()">Update now</button>
              <button type="button" class="btn-secondary" (click)="onLater()">Later</button>
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
      background: var(--backdrop-scrim);
      backdrop-filter: blur(6px);
      -webkit-backdrop-filter: blur(6px);
      animation: backdropIn 200ms ease-out forwards;
      padding: 24px;

      &.closing { animation: backdropOut 150ms ease-in forwards; }
    }

    .modal-card {
      position: relative;
      max-width: 440px;
      width: 100%;
      border-radius: var(--radius-md);
      background: var(--glass-bg);
      backdrop-filter: blur(var(--glass-blur));
      -webkit-backdrop-filter: blur(var(--glass-blur));
      border: 1px solid var(--glass-border);
      overflow: hidden;
      animation: cardIn 250ms ease-out forwards;
      box-shadow: var(--shadow-glass), 0 24px 64px rgba(0, 0, 0, 0.35);

      &.closing { animation: cardOut 150ms ease-in forwards; }
    }

    .accent-bar {
      height: 2px;
      flex-shrink: 0;
      background: linear-gradient(90deg, var(--accent-primary), var(--accent-secondary));
    }

    .card-body {
      padding: 28px 24px 22px;
      display: flex;
      flex-direction: column;
      align-items: center;
      text-align: center;
    }

    .icon-wrap {
      display: flex;
      align-items: center;
      justify-content: center;
      width: 52px;
      height: 52px;
      margin-bottom: 16px;
      border-radius: var(--radius-sm);
      color: var(--accent-primary);
      background: var(--accent-tint-bg);
      border: 1px solid var(--accent-tint-border);
      box-shadow: var(--shadow-glow);
    }

    .title {
      font-family: var(--font-sans);
      font-size: 1.125rem;
      font-weight: 600;
      color: var(--text-primary);
      margin: 0 0 10px;
    }

    .version {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      margin: 0 0 16px;
      font-family: var(--font-mono);
      font-size: 0.8rem;
    }

    .version-label {
      color: var(--text-muted);
      font-weight: 500;
    }

    .version-value {
      color: var(--on-accent-tint);
      background: var(--accent-tint-bg);
      border: 1px solid var(--accent-tint-border);
      padding: 3px 10px;
      border-radius: 999px;
    }

    .notes,
    .notes-fallback {
      width: 100%;
      margin: 0 0 20px;
      text-align: left;
      font-family: var(--font-sans);
      font-size: 0.8125rem;
      line-height: 1.55;
      color: var(--text-secondary);
    }

    .notes {
      max-height: 140px;
      overflow-y: auto;
      padding: 12px 14px;
      border-radius: var(--radius-sm);
      background: var(--surface-inset);
      border: 1px solid var(--glass-border);
    }

    .notes :deep(p) {
      margin: 0 0 8px;
    }

    .notes :deep(p:last-child) {
      margin-bottom: 0;
    }

    .notes :deep(strong) {
      color: var(--text-primary);
      font-weight: 600;
    }

    .notes :deep(a) {
      color: var(--accent-primary);
      text-decoration: none;
      font-weight: 500;
    }

    .notes :deep(a:hover) {
      text-decoration: underline;
    }

    .notes :deep(tt),
    .notes :deep(code) {
      font-family: var(--font-mono);
      font-size: 0.75rem;
      color: var(--text-muted);
      background: var(--overlay-2);
      padding: 1px 4px;
      border-radius: 4px;
    }

    .notes-fallback {
      padding: 12px 14px;
      border-radius: var(--radius-sm);
      background: var(--surface-inset);
      border: 1px solid var(--glass-border);
    }

    .actions {
      display: flex;
      gap: 10px;
      width: 100%;
    }

    .btn-primary {
      flex: 1;
      height: 40px;
      border: none;
      border-radius: var(--radius-sm);
      background: var(--accent-primary);
      color: #0c0d14;
      font-family: var(--font-sans);
      font-size: 0.875rem;
      font-weight: 600;
      cursor: pointer;
      transition: filter 150ms, box-shadow 150ms, transform 150ms;

      &:hover {
        filter: brightness(1.08);
        box-shadow: var(--shadow-glow);
      }
    }

    .btn-secondary {
      flex: 1;
      height: 40px;
      border: 1px solid var(--glass-border);
      border-radius: var(--radius-sm);
      background: transparent;
      color: var(--text-secondary);
      font-family: var(--font-sans);
      font-size: 0.875rem;
      font-weight: 500;
      cursor: pointer;
      transition: background 150ms, border-color 150ms, color 150ms;

      &:hover {
        background: var(--glass-bg-hover);
        border-color: var(--glass-border-strong);
        color: var(--text-primary);
      }
    }

    @keyframes backdropIn { from { opacity: 0; } to { opacity: 1; } }
    @keyframes backdropOut { from { opacity: 1; } to { opacity: 0; } }
    @keyframes cardIn { from { opacity: 0; transform: scale(0.96) translateY(8px); } to { opacity: 1; transform: scale(1) translateY(0); } }
    @keyframes cardOut { from { opacity: 1; transform: scale(1); } to { opacity: 0; transform: scale(0.96); } }
  `,
})
export class UpdateModalComponent {
  private readonly sanitizer = inject(DomSanitizer);

  closing = signal(false);

  constructor(public updateService: UpdateService) {}

  readonly releaseNotesHtml = computed((): SafeHtml | null => {
    const raw = this.updateService.updateInfo()?.releaseNotes;
    if (!raw?.trim()) return null;
    const normalized = normalizeUpdateReleaseNotes(raw);
    const safe = this.sanitizer.sanitize(SecurityContext.HTML, normalized);
    return safe ? this.sanitizer.bypassSecurityTrustHtml(safe) : null;
  });

  onUpdate(): void {
    this.dismiss();
    this.updateService.download();
  }

  onLater(): void {
    this.dismiss();
    this.updateService.dismissModal();
  }

  private dismiss(): void {
    this.closing.set(true);
    setTimeout(() => this.closing.set(false), 150);
  }
}
