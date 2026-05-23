import { Module } from '@nestjs/common';
import { RuntimeService } from './runtime.service';

@Module({
  providers: [RuntimeService],
  exports: [RuntimeService],
})
export class RuntimeModule {}
