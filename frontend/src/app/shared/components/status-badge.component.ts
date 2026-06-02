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
      background: var(--overlay-2);
    }
    .badge-dot {
      width: 6px; height: 6px;
      border-radius: 50%;
      flex-shrink: 0;
    }
    .badge-alive, .badge-healthy, .badge-awake { color: var(--accent-success); }
    .dot-alive, .dot-healthy, .dot-awake { background: var(--accent-success); box-shadow: 0 0 6px rgba(52, 211, 153, 0.5); }
    .badge-chatting { color: var(--accent-primary); }
    .dot-chatting { background: var(--accent-primary); box-shadow: 0 0 6px var(--accent-primary-glow); }
    .badge-sleeping, .badge-drowsy { color: var(--accent-warn); }
    .dot-sleeping, .dot-drowsy { background: var(--accent-warn); box-shadow: 0 0 6px rgba(251, 191, 36, 0.5); }
    .badge-offline, .badge-evicted, .badge-unreachable { color: var(--text-muted); }
    .dot-offline, .dot-evicted, .dot-unreachable { background: #525252; }
    .badge-creating { color: var(--accent-primary); }
    .dot-creating { background: var(--accent-primary); box-shadow: 0 0 6px rgba(167, 139, 250, 0.5); }
    .badge-error, .badge-loading { color: var(--accent-danger); }
    .dot-error, .dot-loading { background: var(--accent-danger); box-shadow: 0 0 6px rgba(248, 113, 113, 0.5); }
  `],
})
export class StatusBadgeComponent {
  @Input() status = 'unknown';
  @Input() label = '';
}
