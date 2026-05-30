import {
  HttpErrorResponse,
  HttpInterceptorFn,
  HttpHandlerFn,
  HttpRequest,
} from '@angular/common/http';
import { inject } from '@angular/core';
import { Router } from '@angular/router';
import { catchError, throwError } from 'rxjs';
import { ToastService } from '../../shared/toast/toast.service';
import {
  billingPaywallHint,
  isBillingPaywallError,
} from '../services/billing-paywall.util';

let lastPaywallToastAt = 0;

export const billingPaywallInterceptor: HttpInterceptorFn = (
  req: HttpRequest<unknown>,
  next: HttpHandlerFn,
) => {
  return next(req).pipe(
    catchError((error: HttpErrorResponse) => {
      if (!isBillingPaywallError(error)) {
        return throwError(() => error);
      }

      const now = Date.now();
      if (now - lastPaywallToastAt > 4000) {
        lastPaywallToastAt = now;
        const toast = inject(ToastService);
        const router = inject(Router);
        toast.show(billingPaywallHint(error), 'error', 6000);
        void router.navigate(['/settings'], {
          queryParams: { section: 'billing' },
        });
      }

      return throwError(() => error);
    }),
  );
};
