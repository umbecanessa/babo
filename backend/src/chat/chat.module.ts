import { Module } from '@nestjs/common';
import { JwtModule } from '@nestjs/jwt';
import { ChatGateway } from './chat.gateway';
import { AgentsModule } from '../agents/agents.module';
import { RuntimeModule } from '../runtime/runtime.module';
import { ChannelsModule } from '../channels/channels.module';

@Module({
  imports: [JwtModule.register({}), AgentsModule, RuntimeModule, ChannelsModule],
  providers: [ChatGateway],
})
export class ChatModule {}
