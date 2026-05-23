export interface User {
  id: string;
  email: string;
  displayName?: string;
  createdAt: string;
}

export interface AuthTokens {
  accessToken: string;
  refreshToken: string;
  userId: string;
}

export interface ApiKey {
  id: string;
  key?: string;  // Only present on creation
  keyPrefix: string;
  name: string;
  rateLimitRpm: number;
  isActive: boolean;
  totalRequests: number;
  lastUsedAt: string | null;
  createdAt: string;
}
