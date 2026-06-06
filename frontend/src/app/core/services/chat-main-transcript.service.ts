import { Injectable, signal } from '@angular/core';
import { ChatMessage } from './websocket.service';

/**
 * Shared in-memory transcript for websocket:main — keeps Chat tab and Projects
 * sidebar aligned while both surfaces are mounted in the same browser session.
 */
@Injectable({ providedIn: 'root' })
export class ChatMainTranscriptService {
  private readonly byAgent = new Map<string, ChatMessage[]>();
  readonly revision = signal(0);

  get(agentId: string): ChatMessage[] {
    if (!agentId) return [];
    return this.byAgent.get(agentId) ?? [];
  }

  has(agentId: string): boolean {
    return (this.byAgent.get(agentId)?.length ?? 0) > 0;
  }

  replace(agentId: string, messages: ChatMessage[]): void {
    if (!agentId) return;
    this.byAgent.set(agentId, structuredClone(messages));
    this.revision.update(v => v + 1);
  }

  clear(agentId: string): void {
    if (!agentId) return;
    this.byAgent.delete(agentId);
    this.revision.update(v => v + 1);
  }
}
