export const environment = {
  production: true,
  apiUrl: (typeof window !== 'undefined' && (window as any).__BABO_ADMIN_API__)
    || '/api',
};
