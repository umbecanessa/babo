/** HTTP statuses that mean the Nest API is up (auth routes reject bad input, not missing). */
export function isBackendReachableStatus(statusCode: number): boolean {
  if (statusCode >= 200 && statusCode < 300) return true;
  return statusCode === 400 || statusCode === 401 || statusCode === 403 || statusCode === 422;
}

export function formatBackendReachabilityMessage(
  ok: boolean,
  latencyMs: number,
  statusCode: number,
  fallback?: string,
): string {
  if (!ok) {
    if (statusCode === 404) return 'Server not found — check the URL';
    if (statusCode >= 500) return `Server error (${statusCode})`;
    return fallback || 'Could not reach server';
  }
  if (latencyMs > 0) return `Server reachable · ${latencyMs} ms`;
  return 'Server reachable';
}
