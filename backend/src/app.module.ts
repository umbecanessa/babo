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

function loadOperatorModule(): unknown[] {
  if (process.env.BILLING_PROVIDER !== 'operator') return [];
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const { OperatorModule } = require('@babo/operator');
    return [OperatorModule.forRoot()];
  } catch (err) {
    console.error(
      'BILLING_PROVIDER=operator but @babo/operator is not installed:',
      (err as Error).message,
    );
    return [];
  }
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
    ...(loadOperatorModule() as never[]),
  ],
})
export class AppModule {}

