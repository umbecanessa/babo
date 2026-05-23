import { Injectable } from '@angular/core';
import { environment } from '../../../environments/environment';

/**
 * Detects whether the app is running inside the Electron desktop shell
 * or a regular web browser.
 *
 * `isElectron`  -- true when running inside the Electron shell.
 * `isRemote`    -- true when running in a regular browser (not Electron).
 *                  In remote mode, all data goes through the NestJS relay.
 */
@Injectable({ providedIn: 'root' })
export class PlatformService {
  readonly isElectron: boolean;
  readonly isRemote: boolean;

  constructor() {
    this.isElectron =
      !!(window as any).nls?.isDesktop ||
      !!(environment as any).electron ||
      /electron/i.test(navigator.userAgent);
    this.isRemote = !this.isElectron;
  }
}
