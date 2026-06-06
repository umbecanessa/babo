import { APP_INITIALIZER, ApplicationConfig, provideZonelessChangeDetection, isDevMode } from '@angular/core';
import { provideRouter, withHashLocation } from '@angular/router';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { provideServiceWorker } from '@angular/service-worker';
import { provideTranslateService } from '@ngx-translate/core';
import { provideTranslateHttpLoader } from '@ngx-translate/http-loader';
import { routes } from './app.routes';
import { jwtInterceptor } from './core/interceptors/jwt.interceptor';
import { billingPaywallInterceptor } from './core/interceptors/billing-paywall.interceptor';
import { ApiService } from './core/services/api.service';
import { BaboCloudProvisionService } from './core/services/babo-cloud-provision.service';
import { isDesktopShell } from './core/desktop-boot';
import { environment } from '../environments/environment';

function initDesktopBootstrap(
  api: ApiService,
  cloudProvision: BaboCloudProvisionService,
): () => Promise<void> {
  return async () => {
    if (!isDesktopShell()) return;
    await api.whenReady();
    await cloudProvision.syncRuntimeAuth();
  };
}

export const appConfig: ApplicationConfig = {
  providers: [
    provideZonelessChangeDetection(),
    // Hash routing is required in Electron's file:// context.  Without it, Angular's
    // HTML5 pushState changes the URL to file:///tasks/agentId etc.  Any reload or
    // accidental navigation at that URL returns an empty HTML document because no such
    // file exists, making the entire app go blank with no error.
    provideRouter(routes, ...(environment.electron || isDesktopShell() ? [withHashLocation()] : [])),
    provideHttpClient(withInterceptors([jwtInterceptor, billingPaywallInterceptor])),
    { provide: APP_INITIALIZER, useFactory: initDesktopBootstrap, deps: [ApiService, BaboCloudProvisionService], multi: true },
    provideTranslateService({
      fallbackLang: 'en',
      loader: provideTranslateHttpLoader({ prefix: './assets/i18n/' }),
    }),
    ...(!environment.electron && !isDesktopShell()
      ? [provideServiceWorker('ngsw-worker.js', {
          enabled: !isDevMode(),
          registrationStrategy: 'registerWhenStable:30000',
        })]
      : []),
  ],
};
