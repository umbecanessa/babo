import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { PlatformService } from '../services/platform.service';

/**
 * Guard that redirects to /setup when the Electron app hasn't
 * completed first-run setup (Python env + runtime config).
 *
 * In browser mode, this guard always passes through.
 */
export const setupGuard: CanActivateFn = async () => {
  const platform = inject(PlatformService);
  const router = inject(Router);

  if (!platform.isElectron) {
    return true;
  }

  const nls = (window as any).nls;
  if (!nls?.setup?.check) {
    return true;
  }

  try {
    const status = await nls.setup.check();
    if (!status.setupComplete) {
      router.navigate(['/setup']);
      return false;
    }
  } catch {
    // If IPC fails, allow through (may be in dev without Electron)
  }

  return true;
};
