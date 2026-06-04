import { Global, Module } from '@nestjs/common';
import { ChannelsController } from './channels.controller';
import { ChannelsService } from './channels.service';
import { DiscordGatewayService } from './discord-gateway.service';
import { RuntimeModule } from '../runtime/runtime.module';
import { AgentsModule } from '../agents/agents.module';
import { PrismaModule } from '../prisma/prisma.module';
import { BaboCloudModule } from '../babo-cloud/babo-cloud.module';

@Global()
@Module({
  imports: [PrismaModule, RuntimeModule, AgentsModule, BaboCloudModule],
  controllers: [ChannelsController],
  providers: [ChannelsService, DiscordGatewayService],
  exports: [ChannelsService, DiscordGatewayService],
})
export class ChannelsModule {}
