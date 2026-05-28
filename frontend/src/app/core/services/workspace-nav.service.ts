import { Injectable } from '@angular/core';
import { Router } from '@angular/router';

/** Open a workspace file from chat/workbench via Projects → Files. */
@Injectable({ providedIn: 'root' })
export class WorkspaceNavService {
  constructor(private readonly router: Router) {}

  openFile(agentId: string, filePath: string): void {
    const path = (filePath || '').trim();
    if (!agentId || !path) return;
    void this.router.navigate(['/projects', agentId], {
      queryParams: { tab: 'files', path },
    });
  }
}
