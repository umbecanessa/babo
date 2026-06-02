import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { PlatformService } from '../services/platform.service';

/** Only allows navigation when running inside the Electron desktop app. */
export const electronGuard: CanActivateFn = () => {
  const platform = inject(PlatformService);
  const router = inject(Router);

  if (platform.isElectron) {
    return true;
  }

  router.navigate(['/dashboard']);
  return false;
};
