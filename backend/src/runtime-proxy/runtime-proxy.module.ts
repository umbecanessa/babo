import { Module } from '@nestjs/common';
import { RuntimeProxyController } from './runtime-proxy.controller';

@Module({
  controllers: [RuntimeProxyController],
})
export class RuntimeProxyModule {}
