import { Injectable } from '@angular/core';

@Injectable({ providedIn: 'root' })
export class OnboardingService {
  private readonly PREFIX = 'nls_onboarding_seen_';

  hasSeen(pageKey: string): boolean {
    return localStorage.getItem(this.PREFIX + pageKey) === 'true';
  }

  markSeen(pageKey: string): void {
    localStorage.setItem(this.PREFIX + pageKey, 'true');
  }

  reset(pageKey: string): void {
    localStorage.removeItem(this.PREFIX + pageKey);
  }

  resetAll(): void {
    Object.keys(localStorage)
      .filter((k) => k.startsWith(this.PREFIX))
      .forEach((k) => localStorage.removeItem(k));
  }
}
