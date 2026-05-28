import { Component, Input } from '@angular/core';
import { CommonModule } from '@angular/common';

@Component({
  selector: 'app-api-error-banner',
  standalone: true,
  imports: [CommonModule],
  template: `
    @if (message) {
      <div class="banner" [class.forbidden]="forbidden">
        <strong>{{ forbidden ? 'Access denied' : 'Could not load data' }}</strong>
        <p>{{ message }}</p>
      </div>
    }
  `,
  styles: [`
    .banner {
      padding: 0.85rem 1rem;
      border-radius: var(--radius-md);
      margin-bottom: 1rem;
      background: rgba(192, 57, 43, 0.08);
      border: 1px solid rgba(192, 57, 43, 0.25);
      font-size: 0.9rem;
    }
    .banner strong { display: block; margin-bottom: 0.25rem; }
    .banner p { margin: 0; color: var(--text-secondary); }
    .forbidden {
      background: rgba(229, 165, 32, 0.1);
      border-color: rgba(229, 165, 32, 0.35);
    }
  `],
})
export class ApiErrorBannerComponent {
  @Input() message: string | null = null;
  @Input() forbidden = false;
}
