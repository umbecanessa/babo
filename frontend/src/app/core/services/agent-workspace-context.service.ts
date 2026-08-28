import { Injectable } from '@angular/core';

/** Per-agent workspace metadata (e.g. plan project_dir for file chips). */
@Injectable({ providedIn: 'root' })
export class AgentWorkspaceContextService {
  private readonly _projectDirByAgent = new Map<string, string>();

  private storageKey(agentId: string): string {
    return `babo:project-dir:${(agentId || '').trim()}`;
  }

  hydrateAgent(agentId: string): void {
    const id = (agentId || '').trim();
    if (!id || this._projectDirByAgent.has(id)) return;
    try {
      const stored = localStorage.getItem(this.storageKey(id));
      if (stored) {
        this._projectDirByAgent.set(id, stored.replace(/\\/g, '/').replace(/^\/+|\/+$/g, ''));
      }
    } catch { /* ignore */ }
  }

  setProjectDir(agentId: string, projectDir: string): void {
    const id = (agentId || '').trim();
    const dir = (projectDir || '').trim().replace(/\\/g, '/').replace(/^\/+|\/+$/g, '');
    if (!id || !dir) return;
    this._projectDirByAgent.set(id, dir);
    try {
      localStorage.setItem(this.storageKey(id), dir);
    } catch { /* ignore */ }
  }

  getProjectDir(agentId: string): string {
    const id = (agentId || '').trim();
    if (!id) return '';
    if (!this._projectDirByAgent.has(id)) {
      this.hydrateAgent(id);
    }
    return this._projectDirByAgent.get(id) || '';
  }

  clearAgent(agentId: string): void {
    const id = (agentId || '').trim();
    if (!id) return;
    this._projectDirByAgent.delete(id);
    try {
      localStorage.removeItem(this.storageKey(id));
    } catch { /* ignore */ }
  }

  /** Parse plan create/read banners: "PROJECT DIRECTORY: foo/" */
  parseProjectDirFromText(text: string): string | null {
    const m = (text || '').match(/PROJECT\s+DIRECTORY:\s*([^\s/\n]+)\/?/i);
    const dir = m?.[1]?.trim();
    return dir || null;
  }

  noteProjectDirFromText(agentId: string, text: string): void {
    const dir = this.parseProjectDirFromText(text);
    if (dir) {
      this.setProjectDir(agentId, dir);
    }
  }
}
