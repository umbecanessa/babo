import { Component, Input, Output, EventEmitter } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

@Component({
  selector: 'app-command-bar',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './command-bar.component.html',
  styleUrl: './command-bar.component.scss',
})
export class CommandBarComponent {
  @Input() agentId = '';
  @Output() command = new EventEmitter<string>();

  message = '';
  sending = false;

  send(): void {
    const msg = this.message.trim();
    if (!msg || this.sending) return;
    this.sending = true;
    this.command.emit(msg);
    this.message = '';
    setTimeout(() => this.sending = false, 1000);
  }

  onKeydown(event: KeyboardEvent): void {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      this.send();
    }
  }
}
