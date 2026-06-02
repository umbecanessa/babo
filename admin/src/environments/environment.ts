export const environment = {
  production: false,
  /** NestJS API base (includes /api prefix). Override via window.__BABO_ADMIN_API__ at deploy time. */
  apiUrl: (typeof window !== 'undefined' && (window as any).__BABO_ADMIN_API__)
    || 'http://localhost:3000/api',
};
