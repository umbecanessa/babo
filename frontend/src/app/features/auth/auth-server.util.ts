import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import {
  formatBackendReachabilityMessage,
  isBackendReachableStatus,
} from '../../core/backend-reachability.util';
import { ApiService } from '../../core/services/api.service';
import { isDesktopShell } from '../../core/desktop-boot';

export interface ServerCheckResult {
  ok: boolean;
  statusCode: number;
  message: string;
}

/**
 * Reachability check using the same API base as AuthService (renderer or main-process ping).
 */
export async function checkAuthServer(
  api: ApiService,
  http: HttpClient,
): Promise<ServerCheckResult> {
  await api.whenReady();
  const apiBase = api.apiBase;

  const nls = (window as unknown as {
    nls?: {
      backend?: {
        ping: (url?: string) => Promise<{
          ok: boolean;
          statusCode: number;
          message: string;
          latency: number;
        }>;
      };
    };
  }).nls;

  if (isDesktopShell() && nls?.backend?.ping) {
    const result = await nls.backend.ping(api.nestjsRoot());
    const ok = result.ok && isBackendReachableStatus(result.statusCode);
    return {
      ok,
      statusCode: result.statusCode,
      message: formatBackendReachabilityMessage(
        ok,
        result.latency,
        result.statusCode,
        result.message,
      ),
    };
  }

  try {
    const res = await firstValueFrom(
      http.post(`${apiBase}/auth/login`, {}, { observe: 'response' }),
    );
    const ok = isBackendReachableStatus(res.status);
    return {
      ok,
      statusCode: res.status,
      message: formatBackendReachabilityMessage(ok, 0, res.status),
    };
  } catch (err: any) {
    const status = err?.status ?? 0;
    const ok = isBackendReachableStatus(status);
    return {
      ok,
      statusCode: status,
      message: formatBackendReachabilityMessage(
        ok,
        0,
        status,
        err?.message || 'Unreachable',
      ),
    };
  }
}
