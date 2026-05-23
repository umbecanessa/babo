import { ApplicationConfig, provideZonelessChangeDetection, isDevMode } from '@angular/core';
import { provideRouter, withHashLocation } from '@angular/router';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { provideServiceWorker } from '@angular/service-worker';
import { provideTranslateService } from '@ngx-translate/core';
import { provideTranslateHttpLoader } from '@ngx-translate/http-loader';
import { routes } from './app.routes';
import { jwtInterceptor } from './core/interceptors/jwt.interceptor';
import { environment } from '../environments/environment';

export const appConfig: ApplicationConfig = {
  providers: [
    provideZonelessChangeDetection(),
    // Hash routing is required in Electron's file:// context.  Without it, Angular's
    // HTML5 pushState changes the URL to file:///tasks/agentId etc.  Any reload or
    // accidental navigation at that URL returns an empty HTML document because no such
    // file exists, making the entire app go blank with no error.
    provideRouter(routes, ...(environment.electron ? [withHashLocation()] : [])),
    provideHttpClient(withInterceptors([jwtInterceptor])),
    provideTranslateService({
      fallbackLang: 'en',
      loader: provideTranslateHttpLoader({ prefix: './assets/i18n/' }),
    }),
    ...(!environment.electron
      ? [provideServiceWorker('ngsw-worker.js', {
          enabled: !isDevMode(),
          registrationStrategy: 'registerWhenStable:30000',
        })]
      : []),
  ],
};
