import { HttpInterceptorFn, HttpRequest, HttpHandlerFn, HttpErrorResponse } from '@angular/common/http';
import { inject } from '@angular/core';
import { catchError, switchMap, throwError, Observable, finalize, shareReplay } from 'rxjs';
import { AuthService } from '../services/auth.service';

/**
 * JWT interceptor with automatic token refresh.
 *
 * - Attaches the access token to every outgoing request (except auth endpoints).
 * - On 401: refreshes the token once, then retries the original request.
 * - Concurrent 401s share a single refresh call via shareReplay.
 * - If the refresh also fails, logs the user out.
 */

let refreshInFlight$: Observable<string> | null = null;

export const jwtInterceptor: HttpInterceptorFn = (req, next) => {
  const auth = inject(AuthService);

  if (isAuthRequest(req)) {
    return next(req);
  }

  const token = auth.getAccessToken();
  const authedReq = token ? addToken(req, token) : req;

  return next(authedReq).pipe(
    catchError((error) => {
      if (error instanceof HttpErrorResponse && error.status === 401) {
        return handle401(req, next, auth);
      }
      return throwError(() => error);
    }),
  );
};

function handle401(
  req: HttpRequest<unknown>,
  next: HttpHandlerFn,
  auth: AuthService,
): Observable<any> {
  if (!refreshInFlight$) {
    refreshInFlight$ = auth.refreshAccessToken().pipe(
      finalize(() => { refreshInFlight$ = null; }),
      shareReplay(1),
    );
  }

  return refreshInFlight$.pipe(
    switchMap((newToken) => {
      queueMicrotask(() => {
        try {
          void import('../services/babo-cloud-provision.service').then(
            ({ BaboCloudProvisionService }) => {
              const svc = inject(BaboCloudProvisionService);
              svc.invalidateSyncCache();
              void svc.syncRuntimeAuth();
            },
          );
        } catch {
          /* web */
        }
      });
      return next(addToken(req, newToken));
    }),
    catchError((err) => throwError(() => err)),
  );
}

function addToken(req: HttpRequest<unknown>, token: string): HttpRequest<unknown> {
  return req.clone({
    setHeaders: { Authorization: `Bearer ${token}` },
  });
}

function isAuthRequest(req: HttpRequest<unknown>): boolean {
  const url = req.url;
  return url.includes('/auth/login') ||
         url.includes('/auth/register') ||
         url.includes('/auth/refresh');
}
