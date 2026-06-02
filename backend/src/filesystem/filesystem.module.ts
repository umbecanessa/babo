import { Module } from '@nestjs/common';
import { FilesystemController } from './filesystem.controller';
import { FilesystemService } from './filesystem.service';
import { RuntimeModule } from '../runtime/runtime.module';

@Module({
  imports: [RuntimeModule],
  controllers: [FilesystemController],
  providers: [FilesystemService],
  exports: [FilesystemService],
})
export class FilesystemModule {}
