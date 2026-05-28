export interface CloudAuthContext {
  userId: string;
  apiKeyId?: string;
  agentId?: string | null;
  authType: 'api_key' | 'jwt';
}

declare global {
  namespace Express {
    interface Request {
      cloudAuth?: CloudAuthContext;
    }
  }
}

export {};
