import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ToastService, Toast } from './toast.service';
import { tagColor, humanType } from '../signal-utils';

@Component({
  selector: 'app-toasts',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="toast-container">
      @for (toast of toastService.toasts(); track toast.id) {
        <div class="toast" [class]="'toast-' + toast.type"
             [class.expanded]="toast.expanded"
             (mouseenter)="toastService.pauseAutoDismiss(toast.id)"
             (mouseleave)="toastService.resumeAutoDismiss(toast.id)">

          <!-- Header row: dot + label + close -->
          <div class="toast-header">
            <span class="toast-dot"></span>
            <span class="toast-label">{{ typeLabel(toast.type) }}</span>
            <button class="toast-close" (click)="$event.stopPropagation(); toastService.dismiss(toast.id)"
                    title="Dismiss">&times;</button>
          </div>

          <!-- Body: click to expand/collapse -->
          <div class="toast-body" (click)="toastService.toggleExpand(toast.id)">
            <span class="toast-msg" [class.clamped]="!toast.expanded">{{ toast.message }}</span>

            <!-- Signal tag pills (visible when expanded) -->
            @if (toast.expanded && toast.tags.length > 0) {
              <div class="toast-tags">
                @for (tag of toast.tags; track $index) {
                  <span class="toast-pill" [style.background]="pillColor(tag.type)">
                    <span class="pill-type">{{ pillLabel(tag.type) }}</span>
                    <span class="pill-text">{{ tag.label }}</span>
                  </span>
                }
              </div>
            }

            <!-- Expand indicator -->
            <span class="toast-chevron">{{ toast.expanded ? '▲' : '▼' }}</span>
          </div>
        </div>
      }
    </div>
  `,
  styles: [`
    .toast-container {
      position: fixed;
      top: 64px;
      left: 20px;
      z-index: 9999;
      display: flex;
      flex-direction: column;
      gap: 8px;
      max-width: 420px;
      min-width: 320px;
    }

    .toast {
      display: flex;
      flex-direction: column;
      padding: 12px 16px;
      background: var(--glass-bg);
      backdrop-filter: blur(20px);
      -webkit-backdrop-filter: blur(20px);
      border: 1px solid var(--overlay-3);
      border-left: 3px solid #9a9aaa;
      border-radius: 10px;
      animation: toast-in 0.3s ease-out;
      transition: all 0.2s ease;
    }

    .toast-dream { border-left-color: #c084fc; }
    .toast-reach_out { border-left-color: var(--accent-success); }
    .toast-info { border-left-color: var(--accent-primary); }
    .toast-error { border-left-color: var(--accent-danger); }

    /* --- Header --- */
    .toast-header {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 6px;
    }

    .toast-dot {
      width: 6px;
      height: 6px;
      border-radius: 50%;
      flex-shrink: 0;
    }

    .toast-dream .toast-dot { background: #c084fc; box-shadow: 0 0 6px rgba(192, 132, 252, 0.5); }
    .toast-reach_out .toast-dot { background: var(--accent-success); box-shadow: 0 0 6px rgba(52, 211, 153, 0.5); }
    .toast-info .toast-dot { background: var(--accent-primary); box-shadow: 0 0 6px var(--accent-primary-glow); }
    .toast-error .toast-dot { background: var(--accent-danger); box-shadow: 0 0 6px rgba(248, 113, 113, 0.5); }

    .toast-label {
      font-size: 10px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: #9a9aaa;
      flex: 1;
    }

    .toast-close {
      background: none;
      border: none;
      color: #525262;
      font-size: 18px;
      line-height: 1;
      cursor: pointer;
      padding: 0 2px;
      transition: color 0.15s;
    }
    .toast-close:hover {
      color: var(--text-secondary);
    }

    /* --- Body --- */
    .toast-body {
      cursor: pointer;
      position: relative;
      padding-right: 20px;
    }

    .toast-msg {
      font-size: 13px;
      color: var(--text-secondary);
      line-height: 1.5;
      white-space: pre-wrap;
      word-break: break-word;
    }

    .toast-msg.clamped {
      overflow: hidden;
      display: -webkit-box;
      -webkit-line-clamp: 3;
      -webkit-box-orient: vertical;
    }

    .toast-chevron {
      position: absolute;
      bottom: 0;
      right: 0;
      font-size: 9px;
      color: #525262;
      transition: color 0.15s;
    }
    .toast-body:hover .toast-chevron {
      color: #9a9aaa;
    }

    /* --- Signal pills --- */
    .toast-tags {
      display: flex;
      flex-wrap: wrap;
      gap: 4px;
      margin-top: 8px;
      padding-top: 8px;
      border-top: 1px solid var(--overlay-2);
    }

    .toast-pill {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 2px 8px;
      border-radius: 12px;
      font-size: 11px;
      line-height: 1.4;
      opacity: 0.85;
    }

    .pill-type {
      font-weight: 600;
      color: rgba(0, 0, 0, 0.7);
    }

    .pill-text {
      color: rgba(0, 0, 0, 0.55);
      max-width: 180px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    /* --- Animation --- */
    @keyframes toast-in {
      from { opacity: 0; transform: translateX(-20px); }
      to { opacity: 1; transform: translateX(0); }
    }
  `],
})
export class ToastComponent {
  constructor(public toastService: ToastService) {}

  typeLabel(type: string): string {
    switch (type) {
      case 'dream': return 'Dream';
      case 'reach_out': return 'Agent';
      case 'error': return 'Error';
      default: return 'Info';
    }
  }

  pillColor(type: string): string {
    return tagColor(type);
  }

  pillLabel(type: string): string {
    return humanType(type);
  }
}
