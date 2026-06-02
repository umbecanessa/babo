import { Injectable, signal } from '@angular/core';
import { io, Socket } from 'socket.io-client';
import { Subject, Observable } from 'rxjs';
import { environment } from '../../../environments/environment';
import { AuthService } from './auth.service';

export interface TerminalOutput {
  type: 'output' | 'exit' | 'error' | 'ready';
  data?: string;
  code?: number;
  message?: string;
}

@Injectable({ providedIn: 'root' })
export class TerminalService {
  private socket: Socket | null = null;
  private outputSubject = new Subject<TerminalOutput>();

  connected = signal(false);
  ready = signal(false);

  constructor(private auth: AuthService) {}

  /** Connect to the terminal WebSocket namespace. */
  connect(): void {
    if (this.socket?.connected) return;

    const token = this.auth.getAccessToken();
    if (!token) return;

    this.socket = io(`${environment.wsUrl}/terminal`, {
      auth: { token },
      transports: ['websocket'],
    });

    this.socket.on('connect', () => {
      this.connected.set(true);
    });

    this.socket.on('disconnect', () => {
      this.connected.set(false);
      this.ready.set(false);
    });

    this.socket.on('terminal:ready', () => {
      this.ready.set(true);
      this.outputSubject.next({ type: 'ready' });
    });

    this.socket.on('terminal:output', (data: { data: string }) => {
      this.outputSubject.next({ type: 'output', data: data.data });
    });

    this.socket.on('terminal:exit', (data: { code: number }) => {
      this.outputSubject.next({ type: 'exit', code: data.code });
    });

    this.socket.on('terminal:error', (data: { message: string }) => {
      this.outputSubject.next({ type: 'error', message: data.message });
    });

    this.socket.on('error', (data: any) => {
      this.outputSubject.next({ type: 'error', message: data.message || 'Connection error' });
    });
  }

  /** Send input data to the terminal. */
  sendInput(data: string): void {
    this.socket?.emit('terminal:input', { data });
  }

  /** Notify the terminal of a resize. */
  resize(cols: number, rows: number): void {
    this.socket?.emit('terminal:resize', { cols, rows });
  }

  /** Change the terminal working directory. */
  setCwd(path: string): void {
    this.socket?.emit('terminal:cwd', { path });
  }

  /** Observable of terminal output events. */
  onOutput(): Observable<TerminalOutput> {
    return this.outputSubject.asObservable();
  }

  /** Disconnect and clean up. */
  disconnect(): void {
    this.socket?.disconnect();
    this.socket = null;
    this.connected.set(false);
    this.ready.set(false);
  }
}
