import { Module } from '@nestjs/common';
import { PrismaModule } from '../prisma/prisma.module';
import { ChannelsModule } from '../channels/channels.module';
import { ClawhubService } from './clawhub.service';
import { ClawhubController } from './clawhub.controller';

@Module({
  imports: [PrismaModule, ChannelsModule],
  controllers: [ClawhubController],
  providers: [ClawhubService],
  exports: [ClawhubService],
})
export class ClawhubModule {}
