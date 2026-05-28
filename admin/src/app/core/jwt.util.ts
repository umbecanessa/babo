/** Decode JWT payload (no signature verify — server is source of truth). */
export function jwtPayload(token: string): Record<string, unknown> | null {
  try {
    const part = token.split('.')[1];
    if (!part) return null;
    const json = atob(part.replace(/-/g, '+').replace(/_/g, '/'));
    return JSON.parse(json) as Record<string, unknown>;
  } catch {
    return null;
  }
}

export function jwtRole(token: string): string | null {
  const role = jwtPayload(token)?.['role'];
  return typeof role === 'string' ? role : null;
}
