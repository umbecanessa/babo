import { Module } from '@nestjs/common';
import { AdminController } from './admin.controller';
import { AdminSetupController } from './admin-setup.controller';
import { AdminService } from './admin.service';
import { RuntimeModule } from '../runtime/runtime.module';
import { AuthModule } from '../auth/auth.module';

@Module({
  imports: [RuntimeModule, AuthModule],
  controllers: [AdminController, AdminSetupController],
  providers: [AdminService],
})
export class AdminModule {}
