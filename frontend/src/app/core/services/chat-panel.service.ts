import { Injectable, computed, signal } from '@angular/core';

export type LeftDockTab = 'workbench' | 'browser';
export type RightDockTab = 'live' | 'inbox' | 'context';
export type NavRailMode = 'expanded' | 'collapsed';

const PREFS_KEY = 'babo_chat_panel_prefs';

interface PanelPrefs {
  leftDock: LeftDockTab | 'closed';
  rightDock: RightDockTab | 'closed';
  navRail: NavRailMode;
  focusMode: boolean;
  mobileSheetTab: RightDockTab;
}

const DEFAULT_PREFS: PanelPrefs = {
  leftDock: 'closed',
  rightDock: 'closed',
  navRail: 'expanded',
  focusMode: false,
  mobileSheetTab: 'live',
};

/**
 * Manages Chat tab dock panels (left: Workbench|Browser, right: Live|Inbox|Context),
 * nav rail collapse, and focus mode. One primary panel per side on narrow viewports.
 */
@Injectable({ providedIn: 'root' })
export class ChatPanelService {
  private readonly _leftDock = signal<LeftDockTab | 'closed'>('closed');
  private readonly _rightDock = signal<RightDockTab | 'closed'>('closed');
  private readonly _navRail = signal<NavRailMode>('expanded');
  private readonly _focusMode = signal(false);
  private readonly _mobileSheetTab = signal<RightDockTab>('live');
  private readonly _inboxBadge = signal(0);
  private readonly _inboxPulse = signal(false);

  readonly leftDock = this._leftDock.asReadonly();
  readonly rightDock = this._rightDock.asReadonly();
  readonly navRail = this._navRail.asReadonly();
  readonly focusMode = this._focusMode.asReadonly();
  readonly mobileSheetTab = this._mobileSheetTab.asReadonly();
  readonly inboxBadge = this._inboxBadge.asReadonly();
  readonly inboxPulse = this._inboxPulse.asReadonly();

  readonly rightDockOpen = computed(() => this._rightDock() !== 'closed');
  readonly leftDockOpen = computed(() => this._leftDock() !== 'closed');

  constructor() {
    this.loadPrefs();
  }

  setInboxBadge(count: number): void {
    this._inboxBadge.set(Math.max(0, count));
  }

  /** Open or toggle a left-dock tab (Workbench / Browser). */
  toggleLeft(tab: LeftDockTab): void {
    if (this._focusMode()) {
      this._focusMode.set(false);
    }
    this._leftDock.update((cur) => (cur === tab ? 'closed' : tab));
    this.persist();
  }

  openLeft(tab: LeftDockTab): void {
    if (this._focusMode()) {
      this._focusMode.set(false);
    }
    this._leftDock.set(tab);
    this.persist();
  }

  closeLeft(): void {
    this._leftDock.set('closed');
    this.persist();
  }

  /** Open or toggle a right-dock tab (Live / Inbox / Context). */
  toggleRight(tab: RightDockTab): void {
    if (this._focusMode()) {
      this._focusMode.set(false);
    }
    this._rightDock.update((cur) => (cur === tab ? 'closed' : tab));
    if (tab === 'inbox') {
      this._inboxPulse.set(false);
    }
    this.persist();
  }

  openRight(tab: RightDockTab): void {
    if (this._focusMode()) {
      this._focusMode.set(false);
    }
    this._rightDock.set(tab);
    if (tab === 'inbox') {
      this._inboxPulse.set(false);
    }
    this.persist();
  }

  closeRight(): void {
    this._rightDock.set('closed');
    this.persist();
  }

  /** Suggest Inbox without stealing focus (badge pulse). */
  suggestInbox(): void {
    this._inboxPulse.set(true);
    if (this._rightDock() === 'closed') {
      // keep closed but draw attention via badge + pulse on header btn
    }
  }

  /** Auto-open Context when entering a surface conversation (once per session). */
  maybeOpenContextForSurface(isSurface: boolean, sessionKey: string): void {
    if (!isSurface || sessionKey === 'websocket:main') return;
    const seenKey = `babo_ctx_seen_${sessionKey}`;
    try {
      if (sessionStorage.getItem(seenKey)) return;
      sessionStorage.setItem(seenKey, '1');
    } catch {
      // ignore
    }
    if (this._rightDock() === 'closed') {
      this.openRight('context');
    }
  }

  toggleNavRail(): void {
    this._navRail.update((m) => (m === 'expanded' ? 'collapsed' : 'expanded'));
    this.persist();
  }

  setNavRail(mode: NavRailMode): void {
    this._navRail.set(mode);
    this.persist();
  }

  toggleFocusMode(): void {
    this._focusMode.update((v) => !v);
    if (this._focusMode()) {
      this._leftDock.set('closed');
      this._rightDock.set('closed');
      this._navRail.set('collapsed');
    }
    this.persist();
  }

  setMobileSheetTab(tab: RightDockTab): void {
    this._mobileSheetTab.set(tab);
    this.persist();
  }

  /** Agentic run started — prefer Workbench on left. */
  onAgenticStart(): void {
    if (!this._focusMode()) {
      this.openLeft('workbench');
    }
  }

  /** Browser navigation — switch left dock to Browser. */
  onBrowserNavigate(): void {
    if (!this._focusMode()) {
      this.openLeft('browser');
    }
  }

  /** ask_user from channel — surface Inbox. */
  onAskUserFromChannel(): void {
    this.setInboxBadge(this._inboxBadge() + 1);
    this.openRight('inbox');
  }

  private loadPrefs(): void {
    try {
      const raw = localStorage.getItem(PREFS_KEY);
      if (!raw) return;
      const p = JSON.parse(raw) as Partial<PanelPrefs>;
      if (p.leftDock) this._leftDock.set(p.leftDock);
      if (p.rightDock) this._rightDock.set(p.rightDock);
      if (p.navRail) this._navRail.set(p.navRail);
      if (p.focusMode != null) this._focusMode.set(p.focusMode);
      if (p.mobileSheetTab) this._mobileSheetTab.set(p.mobileSheetTab);
    } catch {
      // ignore
    }
  }

  private persist(): void {
    try {
      const prefs: PanelPrefs = {
        leftDock: this._leftDock(),
        rightDock: this._rightDock(),
        navRail: this._navRail(),
        focusMode: this._focusMode(),
        mobileSheetTab: this._mobileSheetTab(),
      };
      localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
    } catch {
      // ignore
    }
  }
}
