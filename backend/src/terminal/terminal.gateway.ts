import {
  WebSocketGateway,
  WebSocketServer,
  SubscribeMessage,
  OnGatewayConnection,
  OnGatewayDisconnect,
  ConnectedSocket,
  MessageBody,
} from '@nestjs/websockets';
import { Logger, UnauthorizedException } from '@nestjs/common';
import { JwtService } from '@nestjs/jwt';
import { ConfigService } from '@nestjs/config';
import { Server, Socket } from 'socket.io';
import * as os from 'os';
import * as pty from 'node-pty';

/**
 * Local Terminal WebSocket gateway.
 *
 * Spawns a real pseudo-terminal (PTY) on the user's local machine.
 * This gives proper formatting, colors, line wrapping, and full
 * shell compatibility (PowerShell / bash / zsh).
 *
 * The AI agent executes commands through its own tool system on the
 * FastAPI server -- this terminal is for the user only.
 *
 * Protocol (client <-> gateway):
 *   - Client emits 'terminal:input'  { data: string }
 *   - Client emits 'terminal:resize' { cols: number, rows: number }
 *   - Client emits 'terminal:cwd'    { path: string }
 *   - Gateway emits 'terminal:output' { data: string }
 *   - Gateway emits 'terminal:exit'   { code: number }
 *   - Gateway emits 'terminal:ready'
 */
@WebSocketGateway({
  namespace: '/terminal',
  cors: { origin: '*' },
})
export class TerminalGateway
  implements OnGatewayConnection, OnGatewayDisconnect
{
  @WebSocketServer() server!: Server;
  private readonly logger = new Logger(TerminalGateway.name);
  private ptyProcesses = new Map<string, pty.IPty>();

  constructor(
    private jwt: JwtService,
    private config: ConfigService,
  ) {}

  async handleConnection(client: Socket) {
    try {
      const token =
        client.handshake.auth?.token ||
        client.handshake.headers?.authorization?.replace('Bearer ', '');

      if (!token) throw new UnauthorizedException('No token');

      const payload = this.jwt.verify(token, {
        secret: this.config.get('JWT_SECRET'),
      });

      client.data.userId = payload.sub;
      this.logger.log(`Terminal client connected: ${client.id}`);

      // Determine the shell and default cwd
      const isWindows = os.platform() === 'win32';
      const shell = isWindows ? 'powershell.exe' : (process.env.SHELL || '/bin/bash');
      const cwd = os.homedir();

      // Spawn a real PTY process
      const ptyProcess = pty.spawn(shell, [], {
        name: 'xterm-256color',
        cols: 120,
        rows: 30,
        cwd,
        env: process.env as Record<string, string>,
      });

      this.logger.log(
        `Spawned PTY (pid=${ptyProcess.pid}) shell=${shell} for ${client.id}`,
      );

      // Forward PTY output to the client
      ptyProcess.onData((data: string) => {
        client.emit('terminal:output', { data });
      });

      // Handle PTY exit
      ptyProcess.onExit(({ exitCode }) => {
        this.logger.log(`PTY exited (code=${exitCode}) for ${client.id}`);
        client.emit('terminal:exit', { code: exitCode });
        this.ptyProcesses.delete(client.id);
      });

      this.ptyProcesses.set(client.id, ptyProcess);
      client.emit('terminal:ready');
    } catch (err: any) {
      this.logger.warn(`Terminal auth failed for ${client.id}: ${err.message}`);
      client.emit('error', { message: 'Authentication failed' });
      client.disconnect();
    }
  }

  handleDisconnect(client: Socket) {
    this.logger.log(`Terminal client disconnected: ${client.id}`);
    const proc = this.ptyProcesses.get(client.id);
    if (proc) {
      proc.kill();
      this.ptyProcesses.delete(client.id);
    }
  }

  @SubscribeMessage('terminal:input')
  handleInput(
    @ConnectedSocket() client: Socket,
    @MessageBody() data: { data: string },
  ) {
    const proc = this.ptyProcesses.get(client.id);
    if (proc) {
      proc.write(data.data);
    }
  }

  @SubscribeMessage('terminal:resize')
  handleResize(
    @ConnectedSocket() client: Socket,
    @MessageBody() data: { cols: number; rows: number },
  ) {
    const proc = this.ptyProcesses.get(client.id);
    if (proc && data.cols > 0 && data.rows > 0) {
      try {
        proc.resize(data.cols, data.rows);
      } catch (e) {
        // Resize can fail if process already exited
      }
    }
  }

  @SubscribeMessage('terminal:cwd')
  handleCwd(
    @ConnectedSocket() client: Socket,
    @MessageBody() data: { path: string },
  ) {
    const proc = this.ptyProcesses.get(client.id);
    if (proc) {
      // Send a cd command to the shell
      const isWindows = os.platform() === 'win32';
      const cmd = isWindows
        ? `Set-Location -Path "${data.path}"\r`
        : `cd "${data.path}"\n`;
      proc.write(cmd);
    }
  }
}
