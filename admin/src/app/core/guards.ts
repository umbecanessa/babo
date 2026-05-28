import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { AuthService } from './auth.service';
import { AdminApiService } from './admin-api.service';

async function readSetupStatus(api: AdminApiService): Promise<{
  needsSetup: boolean;
  hasAdmin: boolean;
} | null> {
  try {
    return await api.setupStatus();
  } catch {
    return null;
  }
}

export const authGuard: CanActivateFn = async () => {
  const auth = inject(AuthService);
  const router = inject(Router);
  if (auth.accessToken) return true;

  const api = inject(AdminApiService);
  const status = await readSetupStatus(api);
  if (status?.needsSetup) {
    return router.createUrlTree(['/setup']);
  }
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
  const status = await readSetupStatus(api);
  // Allow setup when API is unreachable (first deploy) or no admin exists yet.
  if (!status || status.needsSetup) return true;
  return router.createUrlTree(['/login']);
};

export const setupRedirectGuard: CanActivateFn = async () => {
  const api = inject(AdminApiService);
  const router = inject(Router);
  const status = await readSetupStatus(api);
  if (status?.needsSetup) return router.createUrlTree(['/setup']);
  return true;
};
