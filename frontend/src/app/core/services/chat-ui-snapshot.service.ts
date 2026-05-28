import { Injectable } from '@angular/core';
import { ChatMessage } from './websocket.service';
import type { WorkbenchEntry } from './chat-workbench.service';
import type { WorkbenchDensity } from './workbench-density.util';
import type { RunViewPersisted } from '../models/run-view.model';

/**
 * Persists Chat UI state across route changes (Chat → Tasks → Chat) so the
 * transcript and Brain sidebar metadata are not lost when the component is destroyed.
 *
 * WebSocket replay only covers payloads that arrived while no Chat replay stream
 * was active; it does not reconstruct the prior in-memory message list.
 */
export interface ChatUiSnapshot {
  messages: ChatMessage[];
  nlsMetadata: any;
  activities: any[];
  daydreams: any[];
  runView?: RunViewPersisted | null;
  latestProbeSignals: {
    signals: Record<string, number>;
    fired: string[];
    iteration: number;
    midGeneration: boolean;
    ts: Date;
  } | null;
  agenticActive: boolean;
  agenticStep: number;
  agenticMaxSteps: number;
  activityStatus: string;
  agenticStopping: boolean;
  backgroundTaskActive: boolean;
  /** Bottom workbench panel (agentic / tool log) */
  workbenchOpen: boolean;
  workbenchEntries: WorkbenchEntry[];
  workbenchDensity?: WorkbenchDensity;
  /** Desktop Neural State sidebar */
  sidebarOpen?: boolean;
}

@Injectable({ providedIn: 'root' })
export class ChatUiSnapshotService {
  private byAgent = new Map<string, ChatUiSnapshot>();

  save(agentId: string, snap: ChatUiSnapshot): void {
    if (!agentId) return;
    this.byAgent.set(agentId, structuredClone(snap));
  }

  take(agentId: string): ChatUiSnapshot | null {
    if (!agentId) return null;
    return this.byAgent.get(agentId) ?? null;
  }

  clearAgent(agentId: string): void {
    this.byAgent.delete(agentId);
  }

  clearAll(): void {
    this.byAgent.clear();
  }
}
