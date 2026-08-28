import { Injectable } from '@angular/core';
import { AuthService } from './auth.service';
import { PlatformService } from './platform.service';
import { TerminalConnection } from './terminal-connection';

export interface TerminalOutput {
  type: 'output' | 'exit' | 'error' | 'ready' | 'mode';
  data?: string;
  code?: number;
  message?: string;
  mode?: 'mirror' | 'waiting' | 'standalone';
}

/**
 * Terminal client factory — each tab gets its own TerminalConnection.
 * Electron: raw WebSocket to Python runtime `/ws/terminal`
 * Browser: Socket.IO `/terminal` namespace on NestJS (node-pty)
 */
@Injectable({ providedIn: 'root' })
export class TerminalService {
  constructor(
    private auth: AuthService,
    private platform: PlatformService,
  ) {}

  createConnection(): TerminalConnection {
    return new TerminalConnection(this.auth, this.platform);
  }
}
