export type SortDir = 'asc' | 'desc';

export interface ListQuery {
  search: string;
  filter: string;
  sortKey: string;
  sortDir: SortDir;
  page: number;
  pageSize: number;
}

export const DEFAULT_LIST_QUERY: ListQuery = {
  search: '',
  filter: 'all',
  sortKey: '',
  sortDir: 'desc',
  page: 1,
  pageSize: 25,
};

export function normalizeSearch(s: string): string {
  return s.trim().toLowerCase();
}

export function matchesSearch(haystack: string, query: string): boolean {
  const q = normalizeSearch(query);
  if (!q) return true;
  return haystack.toLowerCase().includes(q);
}

export function paginate<T>(items: T[], page: number, pageSize: number): T[] {
  const p = Math.max(1, page);
  const size = Math.max(1, pageSize);
  const start = (p - 1) * size;
  return items.slice(start, start + size);
}

export function pageCount(total: number, pageSize: number): number {
  return Math.max(1, Math.ceil(total / Math.max(1, pageSize)));
}

export function sortBy<T>(
  items: T[],
  key: string,
  dir: SortDir,
  accessor: (item: T, key: string) => string | number,
): T[] {
  if (!key) return [...items];
  const mul = dir === 'asc' ? 1 : -1;
  return [...items].sort((a, b) => {
    const av = accessor(a, key);
    const bv = accessor(b, key);
    if (typeof av === 'number' && typeof bv === 'number') {
      return (av - bv) * mul;
    }
    return String(av).localeCompare(String(bv)) * mul;
  });
}
