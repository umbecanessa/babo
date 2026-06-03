import { ExecutionContext, Injectable } from '@nestjs/common';
import { AuthGuard } from '@nestjs/passport';
import { Observable, of, isObservable } from 'rxjs';
import { catchError, map } from 'rxjs/operators';

/** Attaches req.user when a valid JWT is present; never blocks anonymous events. */
@Injectable()
export class OptionalJwtAuthGuard extends AuthGuard('jwt') {
  canActivate(context: ExecutionContext): boolean | Promise<boolean> | Observable<boolean> {
    const result = super.canActivate(context);
    if (isObservable(result)) {
      return result.pipe(
        map(() => true),
        catchError(() => of(true)),
      );
    }
    if (result instanceof Promise) {
      return result.then(() => true).catch(() => true);
    }
    return true;
  }

  handleRequest<TUser = any>(_err: any, user: TUser): TUser | null {
    return user ?? null;
  }
}
