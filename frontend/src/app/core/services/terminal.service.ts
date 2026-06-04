import { Injectable, signal } from '@angular/core';
import { io, Socket } from 'socket.io-client';
import { Subject, Observable } from 'rxjs';
import { environment } from '../../../environments/environment';
import { AuthService } from './auth.service';
import { PlatformService } from './platform.service';

export interface TerminalOutput {
  type: 'output' | 'exit' | 'error' | 'ready';
  data?: string;
  code?: number;
  message?: string;
}

/**
 * Terminal client — dual backend:
 * - Electron: raw WebSocket to Python runtime `/ws/terminal`
 * - Browser: Socket.IO `/terminal` namespace on NestJS (node-pty)
 */
@Injectable({ providedIn: 'root' })
export class TerminalService {
  private rawSocket: WebSocket | null = null;
  private ioSocket: Socket | null = null;
  private outputSubject = new Subject<TerminalOutput>();
  private pending: Record<string, unknown>[] = [];
  private runtimeWsBase = '';

  connected = signal(false);
  ready = signal(false);

  constructor(
    private auth: AuthService,
    private platform: PlatformService,
  ) {
    if (this.platform.isElectron) {
      void this.initElectronWsUrl();
    }
  }

  private async initElectronWsUrl(): Promise<void> {
    try {
      const nls = (window as any).nls;
      if (nls?.getUrls) {
        const urls = await nls.getUrls();
        this.runtimeWsBase = urls.wsUrl || '';
      }
    } catch {
      this.runtimeWsBase = '';
    }
    if (!this.runtimeWsBase) {
      this.runtimeWsBase = (environment.wsUrl || 'ws://127.0.0.1:9222').replace(/^http/i, 'ws');
    }
  }

  /** Connect to the terminal backend. Safe to call multiple times. */
  async connect(): Promise<void> {
    if (this.platform.isElectron) {
      if (!this.runtimeWsBase) {
        await this.initElectronWsUrl();
      }
      this.connectRawWs(`${this.runtimeWsBase}/ws/terminal`);
      return;
    }
    this.connectSocketIo();
  }

  sendInput(data: string): void {
    if (this.rawSocket) {
      this.sendRaw({ type: 'input', data });
      return;
    }
    this.ioSocket?.emit('terminal:input', { data });
  }

  resize(cols: number, rows: number): void {
    if (this.rawSocket) {
      this.sendRaw({ type: 'resize', cols, rows });
      return;
    }
    this.ioSocket?.emit('terminal:resize', { cols, rows });
  }

  setCwd(path: string): void {
    if (!path) return;
    if (this.rawSocket) {
      this.sendRaw({ type: 'cwd', path });
      return;
    }
    this.ioSocket?.emit('terminal:cwd', { path });
  }

  onOutput(): Observable<TerminalOutput> {
    return this.outputSubject.asObservable();
  }

  disconnect(): void {
    this.pending = [];
    this.rawSocket?.close();
    this.rawSocket = null;
    this.ioSocket?.disconnect();
    this.ioSocket = null;
    this.connected.set(false);
    this.ready.set(false);
  }

  private connectRawWs(url: string): void {
    if (this.rawSocket?.readyState === WebSocket.OPEN) return;
    if (this.rawSocket?.readyState === WebSocket.CONNECTING) return;

    this.ioSocket?.disconnect();
    this.ioSocket = null;

    this.rawSocket = new WebSocket(url);

    this.rawSocket.onopen = () => {
      this.connected.set(true);
      this.ready.set(true);
      this.flushPendingRaw();
      this.outputSubject.next({ type: 'ready' });
    };

    this.rawSocket.onclose = () => {
      this.connected.set(false);
      this.ready.set(false);
      this.rawSocket = null;
    };

    this.rawSocket.onerror = () => {
      this.outputSubject.next({ type: 'error', message: 'Terminal connection error' });
    };

    this.rawSocket.onmessage = (event) => {
      try {
        const msg = JSON.parse(String(event.data));
        switch (msg.type) {
          case 'output':
            this.outputSubject.next({ type: 'output', data: msg.data || '' });
            break;
          case 'exit':
            this.outputSubject.next({ type: 'exit', code: msg.code ?? 0 });
            break;
          case 'error':
            this.outputSubject.next({ type: 'error', message: msg.message || 'Terminal error' });
            break;
          default:
            break;
        }
      } catch {
        this.outputSubject.next({ type: 'output', data: String(event.data) });
      }
    };
  }

  private connectSocketIo(): void {
    if (this.ioSocket?.connected) return;

    this.rawSocket?.close();
    this.rawSocket = null;

    const token = this.auth.getAccessToken();
    if (!token) {
      this.outputSubject.next({ type: 'error', message: 'Sign in required for shell access' });
      return;
    }

    this.ioSocket = io(`${environment.wsUrl}/terminal`, {
      auth: { token },
      transports: ['websocket'],
    });

    this.ioSocket.on('connect', () => {
      this.connected.set(true);
    });

    this.ioSocket.on('disconnect', () => {
      this.connected.set(false);
      this.ready.set(false);
    });

    this.ioSocket.on('terminal:ready', () => {
      this.ready.set(true);
      this.outputSubject.next({ type: 'ready' });
    });

    this.ioSocket.on('terminal:output', (data: { data: string }) => {
      this.outputSubject.next({ type: 'output', data: data.data });
    });

    this.ioSocket.on('terminal:exit', (data: { code: number }) => {
      this.outputSubject.next({ type: 'exit', code: data.code });
    });

    this.ioSocket.on('terminal:error', (data: { message: string }) => {
      this.outputSubject.next({ type: 'error', message: data.message || 'Terminal error' });
    });

    this.ioSocket.on('error', (data: any) => {
      this.outputSubject.next({
        type: 'error',
        message: data?.message || 'Connection error',
      });
    });
  }

  private sendRaw(payload: Record<string, unknown>): void {
    if (this.rawSocket?.readyState === WebSocket.OPEN) {
      this.rawSocket.send(JSON.stringify(payload));
      return;
    }
    this.pending.push(payload);
  }

  private flushPendingRaw(): void {
    if (this.rawSocket?.readyState !== WebSocket.OPEN) return;
    for (const payload of this.pending) {
      this.rawSocket.send(JSON.stringify(payload));
    }
    this.pending = [];
  }
}
