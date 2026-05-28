import { Injectable, signal } from '@angular/core';

export type Day1CoachStep = 'chat' | 'thinking' | 'skills';

const PENDING_KEY = 'nls_day1_coach_pending';
const STEP_KEY = 'nls_day1_coach_step';

@Injectable({ providedIn: 'root' })
export class Day1CoachService {
  readonly activeStep = signal<Day1CoachStep | null>(null);
  /** Bumped when chat view is ready so the overlay re-measures anchors. */
  readonly layoutEpoch = signal(0);

  requestLayoutUpdate(): void {
    this.layoutEpoch.update((n) => n + 1);
  }

  schedule(): void {
    localStorage.setItem(PENDING_KEY, '1');
    localStorage.setItem(STEP_KEY, 'chat');
  }

  isScheduled(): boolean {
    return localStorage.getItem(PENDING_KEY) === '1';
  }

  startIfScheduled(): void {
    if (!this.isScheduled()) {
      this.activeStep.set(null);
      return;
    }
    const step = (localStorage.getItem(STEP_KEY) as Day1CoachStep) || 'chat';
    this.activeStep.set(step);
  }

  advance(): void {
    const order: Day1CoachStep[] = ['chat', 'thinking', 'skills'];
    const cur = this.activeStep() ?? 'chat';
    const idx = order.indexOf(cur);
    if (idx < 0 || idx >= order.length - 1) {
      this.complete();
      return;
    }
    const next = order[idx + 1];
    localStorage.setItem(STEP_KEY, next);
    this.activeStep.set(next);
  }

  skip(): void {
    this.complete();
  }

  complete(): void {
    localStorage.removeItem(PENDING_KEY);
    localStorage.removeItem(STEP_KEY);
    this.activeStep.set(null);
  }
}
