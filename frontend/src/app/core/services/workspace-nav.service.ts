import { Injectable } from '@angular/core';
import { Router } from '@angular/router';
import { WorkspaceTerminalTabsService } from './workspace-terminal-tabs.service';

/** Open a workspace file or terminal from chat/workbench via Projects → Files. */
@Injectable({ providedIn: 'root' })
export class WorkspaceNavService {
  constructor(
    private readonly router: Router,
    private readonly terminalTabs: WorkspaceTerminalTabsService,
  ) {}

  openFile(agentId: string, filePath: string): void {
    const path = (filePath || '').trim();
    if (!agentId || !path) return;
    void this.router.navigate(['/projects', agentId], {
      queryParams: { tab: 'files', path },
      queryParamsHandling: 'merge',
    });
  }

  openTerminal(agentId: string): void {
    if (!agentId) return;
    this.terminalTabs.openPanel(agentId);
    void this.router.navigate(['/projects', agentId], {
      queryParams: { tab: 'files', terminal: 'open' },
      queryParamsHandling: 'merge',
    });
  }

  syncTerminalQuery(agentId: string, open: boolean): void {
    if (!agentId) return;
    void this.router.navigate(['/projects', agentId], {
      queryParams: { terminal: open ? 'open' : null },
      queryParamsHandling: 'merge',
    });
  }
}
