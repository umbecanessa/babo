import { Module } from '@nestjs/common';
import { JwtModule } from '@nestjs/jwt';
import { TerminalGateway } from './terminal.gateway';

@Module({
  imports: [JwtModule.register({})],
  providers: [TerminalGateway],
})
export class TerminalModule {}
