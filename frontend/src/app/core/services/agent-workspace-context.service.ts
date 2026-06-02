import { Injectable } from '@angular/core';

/** Per-agent workspace metadata (e.g. plan project_dir for file chips). */
@Injectable({ providedIn: 'root' })
export class AgentWorkspaceContextService {
  private readonly _projectDirByAgent = new Map<string, string>();

  setProjectDir(agentId: string, projectDir: string): void {
    const id = (agentId || '').trim();
    const dir = (projectDir || '').trim().replace(/\\/g, '/').replace(/^\/+|\/+$/g, '');
    if (!id || !dir) return;
    this._projectDirByAgent.set(id, dir);
  }

  getProjectDir(agentId: string): string {
    return this._projectDirByAgent.get((agentId || '').trim()) || '';
  }

  clearAgent(agentId: string): void {
    this._projectDirByAgent.delete((agentId || '').trim());
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
