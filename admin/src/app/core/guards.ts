import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { AuthService } from './auth.service';
import { AdminApiService } from './admin-api.service';

export const authGuard: CanActivateFn = () => {
  const auth = inject(AuthService);
  const router = inject(Router);
  if (auth.accessToken) return true;
  return router.createUrlTree(['/login']);
};

export const adminGuard: CanActivateFn = () => {
  const auth = inject(AuthService);
  const router = inject(Router);
  if (auth.isAdmin) return true;
  auth.logout();
  return router.createUrlTree(['/login']);
};

export const setupGuard: CanActivateFn = async () => {
  const api = inject(AdminApiService);
  const router = inject(Router);
  const status = await api.setupStatus();
  if (status.needsSetup) return true;
  return router.createUrlTree(['/login']);
};

export const setupRedirectGuard: CanActivateFn = async () => {
  const api = inject(AdminApiService);
  const router = inject(Router);
  const status = await api.setupStatus();
  if (status.needsSetup) return router.createUrlTree(['/setup']);
  return true;
};
