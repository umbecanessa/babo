import { CanActivate, ExecutionContext, Injectable } from '@nestjs/common';
import { Request } from 'express';
import { EntitlementsService } from './entitlements.service';

@Injectable()
export class CloudAccessGuard implements CanActivate {
  constructor(private entitlements: EntitlementsService) {}

  async canActivate(context: ExecutionContext): Promise<boolean> {
    const req = context.switchToHttp().getRequest<Request>();
    const auth = req.cloudAuth;
    if (!auth) return false;
    await this.entitlements.assertCloudAccess(auth.userId);
    return true;
  }
}
