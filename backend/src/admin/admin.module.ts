import { Module } from '@nestjs/common';
import { AdminController } from './admin.controller';
import { AdminService } from './admin.service';
import { RuntimeModule } from '../runtime/runtime.module';

@Module({
  imports: [RuntimeModule],
  controllers: [AdminController],
  providers: [AdminService],
})
export class AdminModule {}
