import { Component, input, output, HostListener } from '@angular/core';
import { CommonModule } from '@angular/common';

@Component({
  selector: 'app-detail-modal',
  standalone: true,
  imports: [CommonModule],
  template: `
    @if (open()) {
      <div class="modal-backdrop" [class.closing]="closing" (click)="dismiss()">
        <div class="modal-panel" (click)="$event.stopPropagation()">
          <div class="modal-top-bar">
            <h2 class="modal-title">{{ title() }}</h2>
            <button class="modal-close" (click)="dismiss()" aria-label="Close">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
              </svg>
            </button>
          </div>
          <div class="modal-body">
            <ng-content />
          </div>
        </div>
      </div>
    }
  `,
  styleUrls: ['./detail-modal.component.scss'],
})
export class DetailModalComponent {
  open = input(false);
  title = input('');
  closed = output<void>();

  closing = false;

  @HostListener('document:keydown.escape')
  onEsc(): void {
    if (this.open()) this.dismiss();
  }

  dismiss(): void {
    this.closing = true;
    setTimeout(() => {
      this.closing = false;
      this.closed.emit();
    }, 180);
  }
}
