import {
  Component,
  HostListener,
  input,
  output,
  ChangeDetectionStrategy,
} from '@angular/core';
import { CommonModule } from '@angular/common';

@Component({
  selector: 'app-confirm-dialog',
  standalone: true,
  imports: [CommonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './confirm-dialog.component.html',
  styleUrl: './confirm-dialog.component.scss',
})
export class ConfirmDialogComponent {
  title = input.required<string>();
  message = input.required<string>();
  confirmLabel = input('Confirm');
  cancelLabel = input('Cancel');
  variant = input<'danger' | 'default'>('default');

  confirmed = output<void>();
  cancelled = output<void>();

  @HostListener('document:keydown.escape')
  onEscape(): void {
    this.cancelled.emit();
  }

  onBackdropClick(): void {
    this.cancelled.emit();
  }

  onCancelClick(event: MouseEvent): void {
    event.stopPropagation();
    this.cancelled.emit();
  }

  onConfirmClick(event: MouseEvent): void {
    event.stopPropagation();
    this.confirmed.emit();
  }
}
