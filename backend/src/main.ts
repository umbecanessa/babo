import { NestFactory } from '@nestjs/core';
import { ValidationPipe } from '@nestjs/common';
import { AppModule } from './app.module';
import { ChannelsService } from './channels/channels.service';
import { ConfigService } from '@nestjs/config';
import { WebSocketServer } from 'ws';

async function bootstrap() {
  const app = await NestFactory.create(AppModule);

  app.enableCors({
    origin: true,
    credentials: true,
  });

  app.useGlobalPipes(
    new ValidationPipe({
      whitelist: true,
      transform: true,
    }),
  );

  app.setGlobalPrefix('api');

  const port = process.env.PORT || 3000;
  await app.listen(port);
  console.log(`NLS Backend running on port ${port}`);

  // ── Channel relay WebSocket server ───────────────────────────
  //
  // Desktop runtimes connect here so NestJS can push inbound
  // webhook payloads in real-time (the runtime is behind NAT and
  // can't be reached by HTTP).

  const httpServer = app.getHttpServer();
  const channels = app.get(ChannelsService);
  const config = app.get(ConfigService);
  const sharedSecret = config.get<string>('RUNTIME_SHARED_SECRET') || '';

  const relayWss = new WebSocketServer({ noServer: true });

  httpServer.on('upgrade', (req: any, socket: any, head: any) => {
    const match = req.url?.match(/^\/api\/channels\/relay\/([^/?]+)/);
    if (!match) return;

    console.log(`[relay-ws] upgrade request for agent ${match[1]}`);

    const secret = new URL(req.url, 'http://localhost').searchParams.get('secret');
    if (sharedSecret && secret !== sharedSecret) {
      console.log(`[relay-ws] auth failed for agent ${match[1]}`);
      socket.write('HTTP/1.1 401 Unauthorized\r\n\r\n');
      socket.destroy();
      return;
    }

    relayWss.handleUpgrade(req, socket, head, (ws) => {
      const agentId = match[1];
      console.log(`[relay-ws] upgraded for agent ${agentId}`);
      relayWss.emit('connection', ws, agentId);
    });
  });

  relayWss.on('connection', (ws: any, agentId: string) => {
    console.log(`[relay-ws] connection established for agent ${agentId}`);
    channels.registerRelaySocket(agentId, ws);

    channels.drainAndPushPending(agentId, ws);

    ws.on('message', (raw: string) => {
      try {
        const msg = JSON.parse(raw.toString());
        channels.handleRelayInbound(agentId, msg);
      } catch (err: any) {
        console.log(`[relay-ws] parse error from ${agentId}: ${err.message}`);
      }
    });

    ws.on('close', (code: number, reason: string) => {
      console.log(`[relay-ws] closed for agent ${agentId} (code=${code}, reason=${reason})`);
      channels.removeRelaySocket(agentId);
    });

    ws.on('error', (err: any) => {
      console.log(`[relay-ws] error for agent ${agentId}: ${err.message}`);
      channels.removeRelaySocket(agentId);
    });

    ws.send(JSON.stringify({ type: 'connected', agentId }));
  });
}
bootstrap();
