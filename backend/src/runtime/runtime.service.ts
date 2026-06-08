import { Injectable, Logger, OnModuleDestroy, Inject, forwardRef } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import WebSocket from 'ws';
import { ChannelsService } from '../channels/channels.service';

/** HTTP/WebSocket bridge to the Babo Python runtime (OpenAI-compatible inference). */
@Injectable()
export class RuntimeService implements OnModuleDestroy {
  private readonly logger = new Logger(RuntimeService.name);
  private readonly baseUrl: string;
  private readonly secret: string;
  private wsConnections = new Map<string, WebSocket>();

  constructor(
    private config: ConfigService,
    @Inject(forwardRef(() => ChannelsService))
    private channels: ChannelsService,
  ) {
    this.baseUrl =
      config.get('RUNTIME_URL') ||
      config.get('BABO_RUNTIME_URL') ||
      'http://127.0.0.1:8443';
    this.secret =
      config.get('RUNTIME_SHARED_SECRET') ||
      config.get('BABO_SHARED_SECRET') ||
      '';
  }

  onModuleDestroy() {
    for (const [, ws] of this.wsConnections) {
      ws.close();
    }
    this.wsConnections.clear();
  }

  private get headers(): Record<string, string> {
    const h: Record<string, string> = { 'Content-Type': 'application/json' };
    if (this.secret) {
      h['X-Runtime-Secret'] = this.secret;
      h['X-Runtime-Secret'] = this.secret;
    }
    return h;
  }

  async getHealth(): Promise<any> {
    const res = await fetch(`${this.baseUrl}/health`);
    return res.json();
  }

  async createAgent(body: Record<string, any>): Promise<any> {
    const res = await fetch(`${this.baseUrl}/agents`, {
      method: 'POST',
      headers: this.headers,
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || `Runtime error: ${res.status}`);
    }
    return res.json();
  }

  async patchAgent(agentId: string, subpath: string, body: Record<string, any>): Promise<any> {
    const res = await fetch(`${this.baseUrl}/agents/${agentId}/${subpath}`, {
      method: 'PATCH',
      headers: this.headers,
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || `Runtime patch error: ${res.status}`);
    }
    return res.json();
  }

  async listGenesis(): Promise<any[]> {
    const res = await fetch(`${this.baseUrl}/agents/genesis`, {
      headers: this.headers,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(this.formatError(err, res.status));
    }
    return res.json();
  }

  async listAgents(): Promise<any[]> {
    const res = await fetch(`${this.baseUrl}/agents`, {
      headers: this.headers,
    });
    return res.json();
  }

  async getAgent(agentId: string): Promise<any> {
    if (this.channels.hasRelaySocket(agentId)) {
      try {
        return await this.channels.proxyHttpViaRelay(agentId, 'GET', `/agents/${agentId}`);
      } catch (relayErr: any) {
        this.logger.warn(`getAgent relay failed for ${agentId}: ${relayErr.message}`);
      }
    }
    const res = await fetch(`${this.baseUrl}/agents/${agentId}`, {
      headers: this.headers,
      signal: AbortSignal.timeout(5000),
    });
    if (!res.ok) throw new Error(`Agent not found: ${agentId}`);
    return res.json();
  }

  async deleteAgent(agentId: string): Promise<void> {
    await fetch(`${this.baseUrl}/agents/${agentId}`, {
      method: 'DELETE',
      headers: this.headers,
    });
  }

  connectChat(
    connectionId: string,
    agentId: string,
    onMessage: (data: any) => void,
    onClose: () => void,
  ): WebSocket {
    const existing = this.wsConnections.get(connectionId);
    if (existing) {
      this.logger.log(`WS replacing stale connection: ${connectionId}`);
      existing.removeAllListeners();
      existing.on('error', () => {});
      try {
        existing.close();
      } catch {
        // Ignore close errors on stale sockets
      }
      this.wsConnections.delete(connectionId);
    }

    const wsUrl = this.baseUrl.replace(/^http/, 'ws');
    const ws = new WebSocket(`${wsUrl}/ws/chat/${agentId}`);

    ws.on('open', () => {
      this.logger.log(`WS connected: ${connectionId} -> ${agentId}`);
    });

    ws.on('message', (data) => {
      try {
        const parsed = JSON.parse(data.toString());
        onMessage(parsed);
      } catch {
        onMessage({ type: 'raw', content: data.toString() });
      }
    });

    ws.on('close', () => {
      this.logger.log(`WS closed: ${connectionId}`);
      this.wsConnections.delete(connectionId);
      onClose();
    });

    ws.on('error', (err) => {
      this.logger.error(`WS error: ${connectionId}`, err.message);
    });

    this.wsConnections.set(connectionId, ws);
    return ws;
  }

  sendMessage(connectionId: string, message: any): void {
    const ws = this.wsConnections.get(connectionId);
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(message));
    }
  }

  disconnectChat(connectionId: string): void {
    const ws = this.wsConnections.get(connectionId);
    if (ws) {
      ws.close();
      this.wsConnections.delete(connectionId);
    }
  }

  private extractAgentIdFromPath(path: string): string | null {
    const m = path.match(/\/(?:admin\/)?agents\/([^/]+)/);
    return m ? m[1] : null;
  }

  async proxyGet(path: string): Promise<any> {
    const agentId = this.extractAgentIdFromPath(path);
    if (agentId && this.channels.hasRelaySocket(agentId)) {
      try {
        return await this.channels.proxyHttpViaRelay(agentId, 'GET', path);
      } catch (relayErr: any) {
        this.logger.warn(`proxyGet relay failed for ${path}: ${relayErr.message}`);
      }
    }
    const res = await fetch(`${this.baseUrl}${path}`, {
      headers: this.headers,
      signal: AbortSignal.timeout(5000),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(this.formatError(err, res.status));
    }
    return res.json();
  }

  async proxyPost(path: string, body?: any): Promise<any> {
    return this.proxyRequest(path, 'POST', body);
  }

  async proxyPatch(path: string, body?: any): Promise<any> {
    return this.proxyRequest(path, 'PATCH', body);
  }

  async proxyDelete(path: string): Promise<any> {
    return this.proxyRequest(path, 'DELETE');
  }

  async proxyRequest(path: string, method: string, body?: any): Promise<any> {
    const agentId = this.extractAgentIdFromPath(path);
    if (agentId && this.channels.hasRelaySocket(agentId)) {
      try {
        return await this.channels.proxyHttpViaRelay(agentId, method, path, body);
      } catch (relayErr: any) {
        this.logger.warn(`proxyRequest relay failed for ${method} ${path}: ${relayErr.message}`);
      }
    }
    const init: RequestInit = {
      method,
      headers: this.headers,
      signal: AbortSignal.timeout(5000),
    };
    if (body !== undefined && method !== 'GET' && method !== 'DELETE') {
      init.body = JSON.stringify(body);
    }
    const res = await fetch(`${this.baseUrl}${path}`, init);
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(this.formatError(err, res.status));
    }
    if (res.status === 204) {
      return {};
    }
    return res.json();
  }

  private formatError(err: any, status: number): string {
    if (typeof err.detail === 'string') return err.detail;
    if (Array.isArray(err.detail)) {
      return err.detail.map((e: any) => `${(e.loc || []).join('.')}: ${e.msg}`).join('; ');
    }
    return `Runtime error: ${status}`;
  }

  async transcribeAudio(audioBuffer: Buffer, filename: string): Promise<any> {
    const formData = new FormData();
    const blob = new Blob([new Uint8Array(audioBuffer)], { type: 'audio/webm' });
    formData.append('audio', blob, filename);

    const headers: Record<string, string> = {};
    if (this.secret) {
      headers['X-Runtime-Secret'] = this.secret;
      headers['X-Runtime-Secret'] = this.secret;
    }

    const res = await fetch(`${this.baseUrl}/transcribe`, {
      method: 'POST',
      headers,
      body: formData,
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || `Transcription error: ${res.status}`);
    }

    return res.json();
  }
}
