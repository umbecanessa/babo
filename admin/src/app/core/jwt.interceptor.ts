import { HttpInterceptorFn, HttpErrorResponse } from '@angular/common/http';
import { inject } from '@angular/core';
import { Router } from '@angular/router';
import { catchError, throwError } from 'rxjs';
import { AuthService } from './auth.service';

const PUBLIC_PATHS = ['/auth/admin/login', '/auth/login', '/admin/setup'];

export const jwtInterceptor: HttpInterceptorFn = (req, next) => {
  const auth = inject(AuthService);
  const router = inject(Router);
  const isPublic = PUBLIC_PATHS.some((p) => req.url.includes(p));
  const token = auth.accessToken;

  const outgoing =
    token && !isPublic
      ? req.clone({ setHeaders: { Authorization: `Bearer ${token}` } })
      : req;

  return next(outgoing).pipe(
    catchError((err: HttpErrorResponse) => {
      if (
        !isPublic &&
        auth.accessToken &&
        (err.status === 401 || err.status === 403)
      ) {
        auth.logout();
        router.navigate(['/login'], {
          queryParams: { reason: err.status === 403 ? 'forbidden' : 'session' },
        });
      }
      return throwError(() => err);
    }),
  );
};
