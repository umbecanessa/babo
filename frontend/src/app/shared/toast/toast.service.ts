import { Injectable, signal } from '@angular/core';
import { SignalTag } from '../signal-utils';

export type ToastType = 'dream' | 'reach_out' | 'info' | 'error';

export interface Toast {
  id: number;
  message: string;
  type: ToastType;
  /** Auto-dismiss timeout in ms. */
  duration: number;
  /** Whether the toast is expanded to show full content. */
  expanded: boolean;
  /** Parsed signal tags extracted from the original message. */
  tags: SignalTag[];
}

@Injectable({ providedIn: 'root' })
export class ToastService {
  readonly toasts = signal<Toast[]>([]);
  private nextId = 0;
  private timers = new Map<number, ReturnType<typeof setTimeout>>();

  /**
   * Show a toast notification.
   *
   * @param message - Text to display (should already be stripped of raw tags)
   * @param type - Visual variant (dream = purple, reach_out = green, etc.)
   * @param duration - Auto-dismiss after this many ms (default 6000)
   * @param tags - Parsed signal tags to show when expanded
   */
  show(
    message: string,
    type: ToastType = 'info',
    duration = 6000,
    tags: SignalTag[] = [],
  ): void {
    const id = this.nextId++;
    const toast: Toast = { id, message, type, duration, expanded: false, tags };

    this.toasts.update(list => [...list, toast]);

    // Auto-dismiss
    if (duration > 0) {
      this.startTimer(id, duration);
    }
  }

  dismiss(id: number): void {
    this.clearTimer(id);
    this.toasts.update(list => list.filter(t => t.id !== id));
  }

  /** Dismiss every visible toast (e.g. after budget / ask_user resolved). */
  dismissAll(): void {
    for (const id of [...this.timers.keys()]) {
      this.clearTimer(id);
    }
    this.toasts.set([]);
  }

  toggleExpand(id: number): void {
    this.toasts.update(list =>
      list.map(t => (t.id === id ? { ...t, expanded: !t.expanded } : t)),
    );
  }

  /** Pause auto-dismiss (e.g. on hover). */
  pauseAutoDismiss(id: number): void {
    this.clearTimer(id);
  }

  /** Resume auto-dismiss with the remaining duration (simplified: restarts full duration). */
  resumeAutoDismiss(id: number): void {
    const toast = this.toasts().find(t => t.id === id);
    if (toast && toast.duration > 0 && !toast.expanded) {
      this.startTimer(id, toast.duration);
    }
  }

  private startTimer(id: number, ms: number): void {
    this.clearTimer(id);
    const handle = setTimeout(() => {
      this.timers.delete(id);
      this.dismiss(id);
    }, ms);
    this.timers.set(id, handle);
  }

  private clearTimer(id: number): void {
    const existing = this.timers.get(id);
    if (existing) {
      clearTimeout(existing);
      this.timers.delete(id);
    }
  }
}
