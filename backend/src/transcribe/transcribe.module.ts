import { Module } from '@nestjs/common';
import { MulterModule } from '@nestjs/platform-express';
import { TranscribeController } from './transcribe.controller';
import { RuntimeModule } from '../runtime/runtime.module';

@Module({
  imports: [
    RuntimeModule,
    MulterModule.register({
      limits: { fileSize: 25 * 1024 * 1024 }, // 25MB max
    }),
  ],
  controllers: [TranscribeController],
})
export class TranscribeModule {}
