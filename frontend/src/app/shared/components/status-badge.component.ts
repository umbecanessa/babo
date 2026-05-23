import { Component, Input } from '@angular/core';
import { CommonModule } from '@angular/common';

@Component({
  selector: 'app-status-badge',
  standalone: true,
  imports: [CommonModule],
  template: `
    <span class="badge" [ngClass]="'badge-' + status">
      <span class="badge-dot" [ngClass]="'dot-' + status"></span>
      {{ label || status }}
    </span>
  `,
  styles: [`
    .badge {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.1em;
      padding: 4px 10px;
      border-radius: 20px;
      background: rgba(255, 255, 255, 0.04);
    }
    .badge-dot {
      width: 6px; height: 6px;
      border-radius: 50%;
      flex-shrink: 0;
    }
    .badge-alive, .badge-healthy, .badge-awake { color: #34d399; }
    .dot-alive, .dot-healthy, .dot-awake { background: #34d399; box-shadow: 0 0 6px rgba(52, 211, 153, 0.5); }
    .badge-chatting { color: #38bdf8; }
    .dot-chatting { background: #38bdf8; box-shadow: 0 0 6px rgba(56, 189, 248, 0.5); }
    .badge-sleeping, .badge-drowsy { color: #fbbf24; }
    .dot-sleeping, .dot-drowsy { background: #fbbf24; box-shadow: 0 0 6px rgba(251, 191, 36, 0.5); }
    .badge-offline, .badge-evicted, .badge-unreachable { color: #8a8a9a; }
    .dot-offline, .dot-evicted, .dot-unreachable { background: #525252; }
    .badge-creating { color: #a78bfa; }
    .dot-creating { background: #a78bfa; box-shadow: 0 0 6px rgba(167, 139, 250, 0.5); }
    .badge-error, .badge-loading { color: #f87171; }
    .dot-error, .dot-loading { background: #f87171; box-shadow: 0 0 6px rgba(248, 113, 113, 0.5); }
  `],
})
export class StatusBadgeComponent {
  @Input() status = 'unknown';
  @Input() label = '';
}
