import {
  CanActivate,
  ExecutionContext,
  Injectable,
  UnauthorizedException,
} from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { Reflector } from '@nestjs/core';
import { JwtService } from '@nestjs/jwt';
import { Request } from 'express';
import { ApiKeysService } from '../api-keys/api-keys.service';
import { CLOUD_SCOPE_KEY } from './cloud-scope.decorator';
import { CloudAuthContext } from './cloud-auth.types';

@Injectable()
export class CloudAuthGuard implements CanActivate {
  private readonly keyPrefix: string;

  constructor(
    private apiKeys: ApiKeysService,
    private jwt: JwtService,
    private config: ConfigService,
    private reflector: Reflector,
  ) {
    this.keyPrefix = this.config.get<string>('NLS_API_KEY_PREFIX') || 'nlsk_';
  }

  async canActivate(context: ExecutionContext): Promise<boolean> {
    const req = context.switchToHttp().getRequest<Request>();
    const header = req.headers.authorization;
    if (!header?.startsWith('Bearer ')) {
      throw new UnauthorizedException('Bearer token required');
    }

    const token = header.slice(7).trim();
    const scopes =
      this.reflector.get<string[]>(CLOUD_SCOPE_KEY, context.getHandler()) ||
      this.reflector.get<string[]>(CLOUD_SCOPE_KEY, context.getClass()) ||
      ['inference'];

    const agentHeader = req.headers['x-babo-agent-id'];
    const agentFromHeader =
      typeof agentHeader === 'string' && agentHeader.length > 0
        ? agentHeader
        : undefined;

    if (token.startsWith(this.keyPrefix)) {
      const key = await this.apiKeys.validateKey(token, scopes);
      if (!key) {
        throw new UnauthorizedException('Invalid API key or scope');
      }
      await this.apiKeys.touchKey(key.id);
      req.cloudAuth = {
        userId: key.userId,
        apiKeyId: key.id,
        agentId: key.agentId ?? agentFromHeader ?? null,
        authType: 'api_key',
      };
      return true;
    }

    try {
      const payload = this.jwt.verify<{ sub: string }>(token, {
        secret: this.config.get<string>('JWT_SECRET'),
      });
      req.cloudAuth = {
        userId: payload.sub,
        agentId: agentFromHeader ?? null,
        authType: 'jwt',
      };
      return true;
    } catch {
      throw new UnauthorizedException('Invalid or expired access token');
    }
  }
}
