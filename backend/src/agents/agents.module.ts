import { Module } from '@nestjs/common';
import { AgentsController } from './agents.controller';
import { ToolsController } from './tools.controller';
import { AgentsService } from './agents.service';
import { RuntimeModule } from '../runtime/runtime.module';

@Module({
  imports: [RuntimeModule],
  controllers: [AgentsController, ToolsController],
  providers: [AgentsService],
  exports: [AgentsService],
})
export class AgentsModule {}
