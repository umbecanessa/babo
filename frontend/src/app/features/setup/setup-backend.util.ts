/** NestJS backend URL — stored without `/api`; clients append `/api` (see ApiService.nestjsApiBase). */

export const BABO_CLOUD_BACKEND_URL = 'https://api.babo.agency';
export const LOCAL_BACKEND_URL = 'http://localhost:3000';

export type BackendChoiceId = 'babo_cloud' | 'local' | 'custom';

export interface BackendChoice {
  id: BackendChoiceId;
  shortLabel: string;
  description: string;
  url: string;
  recommended?: boolean;
}

export const BACKEND_CHOICES: BackendChoice[] = [
  {
    id: 'babo_cloud',
    shortLabel: 'Babo Cloud',
    description:
      'Account sync, hosted models, and remote access at api.babo.agency — agents run on your devices.',
    url: BABO_CLOUD_BACKEND_URL,
  },
  {
    id: 'local',
    shortLabel: 'This computer',
    description:
      'NestJS on localhost:3000 — your account lives on this PC. Chat can still use Ollama or LAN.',
    url: LOCAL_BACKEND_URL,
  },
  {
    id: 'custom',
    shortLabel: 'My server',
    description:
      'NestJS you deploy (Railway, VPS, homelab). Chat and vision can stay on this PC or your LAN.',
    url: '',
  },
];

export function normalizeNestjsUrl(url: string): string {
  return url.trim().replace(/\/+$/, '').replace(/\/api$/i, '');
}

export function matchBackendChoice(url: string): BackendChoiceId {
  const n = normalizeNestjsUrl(url).toLowerCase();
  if (n === BABO_CLOUD_BACKEND_URL.toLowerCase() || n.includes('api.babo.agency')) {
    return 'babo_cloud';
  }
  if (
    n === LOCAL_BACKEND_URL.toLowerCase() ||
    n.includes('localhost') ||
    n.includes('127.0.0.1')
  ) {
    return 'local';
  }
  if (!n) {
    return 'babo_cloud';
  }
  return 'custom';
}

export function backendDisplayLabel(url: string, choiceId: BackendChoiceId): string {
  if (choiceId === 'babo_cloud') return 'Babo Cloud';
  if (choiceId === 'local') return 'This computer';
  const host = normalizeNestjsUrl(url);
  try {
    return new URL(host).host;
  } catch {
    return host || 'My server';
  }
}
