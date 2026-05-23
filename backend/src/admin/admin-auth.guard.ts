import { Injectable, CanActivate, ExecutionContext, ForbiddenException } from '@nestjs/common';
import { JwtAuthGuard } from '../auth/jwt-auth.guard';

/**
 * Admin guard: extends JWT auth and checks role === 'admin'.
 *
 * Usage:
 *   @UseGuards(AdminAuthGuard)
 *   @Controller('admin')
 */
@Injectable()
export class AdminAuthGuard extends JwtAuthGuard implements CanActivate {
  async canActivate(context: ExecutionContext): Promise<boolean> {
    // First, run the JWT validation
    const isJwtValid = await super.canActivate(context);
    if (!isJwtValid) return false;

    // Then check admin role
    const request = context.switchToHttp().getRequest();
    const user = request.user;

    if (!user || user.role !== 'admin') {
      throw new ForbiddenException('Admin access required');
    }

    return true;
  }
}
