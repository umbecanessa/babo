import {
  Component,
  HostListener,
  input,
  output,
  signal,
  OnInit,
  ChangeDetectionStrategy,
} from '@angular/core';
import { CommonModule } from '@angular/common';

export interface ConfirmDialogResult {
  optionChecked: boolean;
}

@Component({
  selector: 'app-confirm-dialog',
  standalone: true,
  imports: [CommonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './confirm-dialog.component.html',
  styleUrl: './confirm-dialog.component.scss',
})
export class ConfirmDialogComponent implements OnInit {
  title = input.required<string>();
  message = input.required<string>();
  confirmLabel = input('Confirm');
  cancelLabel = input('Cancel');
  variant = input<'danger' | 'default'>('default');
  /** Optional checkbox below the message (e.g. delete squad + agents). */
  showOption = input(false);
  optionLabel = input('');
  optionDefault = input(false);

  confirmed = output<ConfirmDialogResult>();
  cancelled = output<void>();

  optionChecked = signal(false);

  ngOnInit(): void {
    this.optionChecked.set(this.optionDefault());
  }

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
    this.confirmed.emit({
      optionChecked: this.showOption() ? this.optionChecked() : false,
    });
  }

  onOptionChange(event: Event): void {
    const target = event.target as HTMLInputElement;
    this.optionChecked.set(target.checked);
  }
}
