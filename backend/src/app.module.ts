import { Module } from '@nestjs/common';
import { ConfigModule } from '@nestjs/config';
import { PrismaModule } from './prisma/prisma.module';
import { AuthModule } from './auth/auth.module';
import { UsersModule } from './users/users.module';
import { RuntimeModule } from './runtime/runtime.module';
import { AgentsModule } from './agents/agents.module';
import { ChatModule } from './chat/chat.module';
import { ApiKeysModule } from './api-keys/api-keys.module';
import { TranscribeModule } from './transcribe/transcribe.module';
import { AdminModule } from './admin/admin.module';
import { FilesystemModule } from './filesystem/filesystem.module';
import { TerminalModule } from './terminal/terminal.module';
import { SettingsModule } from './settings/settings.module';
import { ChannelsModule } from './channels/channels.module';
import { RuntimeProxyModule } from './runtime-proxy/runtime-proxy.module';
import { ClawhubModule } from './clawhub/clawhub.module';
import { BaboCloudModule } from './babo-cloud/babo-cloud.module';
import { AnalyticsModule } from './analytics/analytics.module';
import { PrismaService } from './prisma/prisma.service';
import * as path from 'path';

function loadOperatorModule(): unknown[] {
  if (process.env.BILLING_PROVIDER !== 'operator') return [];

  const candidates = [
    '@babo/operator',
    path.join(__dirname, '..', 'babo-operator'),
  ];

  for (const mod of candidates) {
    try {
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const { OperatorModule } = require(mod);
      console.log(`[operator] Loaded OperatorModule from ${mod}`);
      return [
        OperatorModule.forRoot({ prismaService: PrismaService }),
      ];
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error(`[operator] Failed to activate from ${mod}: ${msg}`);
    }
  }

  throw new Error(
    'BILLING_PROVIDER=operator but @babo/operator could not be loaded. ' +
      'Ensure GITHUB_TOKEN is set and scripts/ensure-operator.js ran successfully.',
  );
}

@Module({
  imports: [
    ConfigModule.forRoot({ isGlobal: true }),
    PrismaModule,
    AuthModule,
    UsersModule,
    RuntimeModule,
    AgentsModule,
    ChatModule,
    ApiKeysModule,
    TranscribeModule,
    AdminModule,
    FilesystemModule,
    TerminalModule,
    SettingsModule,
    ChannelsModule,
    RuntimeProxyModule,
    ClawhubModule,
    BaboCloudModule,
    AnalyticsModule,
    ...(loadOperatorModule() as never[]),
  ],
})
export class AppModule {}
