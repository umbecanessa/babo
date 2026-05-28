import { Injectable, signal, computed, effect, inject } from '@angular/core';
import { DOCUMENT } from '@angular/common';

export type ThemeMode = 'light' | 'dark' | 'system';

/**
 * App theme controller — three modes:
 *   - 'light'  : pinned to the light palette regardless of OS
 *   - 'dark'   : pinned to the dark palette regardless of OS
 *   - 'system' : follow `prefers-color-scheme` and react to OS changes
 *
 * Mode is persisted in localStorage under `babo_theme` (default 'dark').
 * The service writes `data-theme` on <html> on every change.
 */
@Injectable({ providedIn: 'root' })
export class ThemeService {
  private readonly STORAGE_KEY = 'babo_theme';

  private readonly doc = inject(DOCUMENT);
  private readonly mql: MediaQueryList | null = (() => {
    try {
      return typeof window !== 'undefined' && window.matchMedia
        ? window.matchMedia('(prefers-color-scheme: dark)')
        : null;
    } catch {
      return null;
    }
  })();

  private readonly _mode = signal<ThemeMode>(this.loadStoredMode());
  private readonly _systemDark = signal<boolean>(this.mql?.matches ?? false);

  readonly mode = this._mode.asReadonly();

  readonly effective = computed<'light' | 'dark'>(() => {
    const m = this._mode();
    if (m === 'light' || m === 'dark') return m;
    return this._systemDark() ? 'dark' : 'light';
  });

  constructor() {
    if (this.mql) {
      const listener = (e: MediaQueryListEvent) => this._systemDark.set(e.matches);
      try {
        this.mql.addEventListener('change', listener);
      } catch {
        this.mql.addListener(listener as never);
      }
    }

    effect(() => {
      const m = this._mode();
      this.doc.documentElement.setAttribute('data-theme', m);
      try {
        localStorage.setItem(this.STORAGE_KEY, m);
      } catch {
        // private browsing / quota
      }
    });
  }

  setMode(mode: ThemeMode) {
    this._mode.set(mode);
  }

  toggle() {
    const current = this.effective();
    this.setMode(current === 'dark' ? 'light' : 'dark');
  }

  /** light → dark → system → light */
  cycle() {
    const m = this._mode();
    if (m === 'light') this.setMode('dark');
    else if (m === 'dark') this.setMode('system');
    else this.setMode('light');
  }

  themeCycleTooltip(): string {
    const m = this._mode();
    if (m === 'light') return 'Theme: Light — click for Dark';
    if (m === 'dark') return 'Theme: Dark — click for System';
    return 'Theme: System — click for Light';
  }

  private loadStoredMode(): ThemeMode {
    try {
      const raw = localStorage.getItem(this.STORAGE_KEY);
      if (raw === 'light' || raw === 'dark' || raw === 'system') return raw;
    } catch {
      // ignore
    }
    return 'dark';
  }
}
