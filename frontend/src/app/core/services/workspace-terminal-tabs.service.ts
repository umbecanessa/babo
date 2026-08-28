import { Injectable, Signal, WritableSignal, computed, inject, signal } from '@angular/core';
import { PlatformService } from './platform.service';

export type WorkspaceTerminalTabKind = 'agent' | 'standalone';

export interface WorkspaceTerminalTab {
  id: string;
  kind: WorkspaceTerminalTabKind;
  label: string;
}

interface AgentTerminalState {
  tabs: WritableSignal<WorkspaceTerminalTab[]>;
  activeTabId: WritableSignal<string>;
  panelOpen: WritableSignal<boolean>;
  tabCount: Signal<number>;
  openTabCount: Signal<number>;
}

/** Cursor-style terminal tabs per agent (agent PTY mirror + optional shells). */
@Injectable({ providedIn: 'root' })
export class WorkspaceTerminalTabsService {
  private readonly platform = inject(PlatformService);
  private readonly states = new Map<string, AgentTerminalState>();
  private nextTabId = 1;

  /** Electron desktop can mirror the agent PTY; browser uses standalone shells only. */
  readonly agentMirrorSupported = !this.platform.isRemote;

  tabs(agentId: string): Signal<WorkspaceTerminalTab[]> {
    return this.state(agentId).tabs;
  }

  activeTabId(agentId: string): Signal<string> {
    return this.state(agentId).activeTabId;
  }

  panelOpen(agentId: string): Signal<boolean> {
    return this.state(agentId).panelOpen;
  }

  tabCount(agentId: string): Signal<number> {
    return this.state(agentId).tabCount;
  }

  openTabCount(agentId: string): Signal<number> {
    return this.state(agentId).openTabCount;
  }

  openPanel(agentId: string): void {
    const state = this.state(agentId);
    if (this.agentMirrorSupported) {
      this.ensureAgentTab(agentId);
    } else if (state.tabs().length === 0) {
      this.addStandaloneTab(agentId);
      return;
    }
    state.panelOpen.set(true);
  }

  setPanelOpen(agentId: string, open: boolean): void {
    const state = this.state(agentId);
    if (open) {
      this.openPanel(agentId);
      return;
    }
    state.panelOpen.set(false);
  }

  togglePanel(agentId: string): boolean {
    const state = this.state(agentId);
    if (state.panelOpen()) {
      state.panelOpen.set(false);
      return false;
    }
    this.openPanel(agentId);
    return true;
  }

  selectTab(agentId: string, tabId: string): void {
    const state = this.state(agentId);
    if (state.tabs().some((tab) => tab.id === tabId)) {
      state.activeTabId.set(tabId);
    }
  }

  addStandaloneTab(agentId: string): string {
    const state = this.state(agentId);
    const standaloneCount = state.tabs().filter((tab) => tab.kind === 'standalone').length;
    const id = this.newTabId();
    const label = standaloneCount === 0 ? 'Terminal 1' : `Terminal ${standaloneCount + 1}`;
    state.tabs.update((tabs) => [...tabs, { id, kind: 'standalone', label }]);
    state.activeTabId.set(id);
    state.panelOpen.set(true);
    return id;
  }

  closeTab(agentId: string, tabId: string): boolean {
    const state = this.state(agentId);
    const tabs = state.tabs();
    const tab = tabs.find((entry) => entry.id === tabId);
    if (!tab) return state.panelOpen();

    if (tab.kind === 'agent' && tabs.length === 1) {
      state.tabs.set([]);
      state.activeTabId.set('');
      state.panelOpen.set(false);
      return false;
    }

    const next = tabs.filter((entry) => entry.id !== tabId);
    state.tabs.set(next);
    if (state.activeTabId() === tabId) {
      state.activeTabId.set(next[next.length - 1]?.id || '');
    }
    if (next.length === 0) {
      state.panelOpen.set(false);
      return false;
    }
    return state.panelOpen();
  }

  ensureAgentTab(agentId: string): WorkspaceTerminalTab | null {
    if (!this.agentMirrorSupported) {
      return null;
    }
    const state = this.state(agentId);
    const existing = state.tabs().find((tab) => tab.kind === 'agent');
    if (existing) {
      if (!state.activeTabId()) {
        state.activeTabId.set(existing.id);
      }
      return existing;
    }

    const tab: WorkspaceTerminalTab = {
      id: this.newTabId(),
      kind: 'agent',
      label: 'Agent Shell',
    };
    state.tabs.update((tabs) => [tab, ...tabs]);
    state.activeTabId.set(tab.id);
    return tab;
  }

  clearAgent(agentId: string): void {
    const key = (agentId || '').trim();
    if (key) {
      this.states.delete(key);
    }
  }

  private state(agentId: string): AgentTerminalState {
    const key = (agentId || '').trim();
    if (!key) {
      throw new Error('agentId required for terminal tabs');
    }
    let entry = this.states.get(key);
    if (!entry) {
      const tabs = signal<WorkspaceTerminalTab[]>([]);
      const panelOpen = signal(false);
      const activeTabId = signal('');
      entry = {
        tabs,
        activeTabId,
        panelOpen,
        tabCount: computed(() => tabs().length),
        openTabCount: computed(() => (panelOpen() ? tabs().length : 0)),
      };
      this.states.set(key, entry);
    }
    return entry;
  }

  private newTabId(): string {
    return `term-${this.nextTabId++}`;
  }
}
