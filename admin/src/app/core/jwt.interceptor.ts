import { HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { AuthService } from './auth.service';

export const jwtInterceptor: HttpInterceptorFn = (req, next) => {
  const auth = inject(AuthService);
  const token = auth.accessToken;
  if (!token || req.url.includes('/auth/login') || req.url.includes('/admin/setup')) {
    return next(req);
  }
  return next(
    req.clone({ setHeaders: { Authorization: `Bearer ${token}` } }),
  );
};
