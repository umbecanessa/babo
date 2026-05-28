export function formatNumber(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return '—';
  return new Intl.NumberFormat().format(n);
}

export function runtimeStatusLabel(agent: any): string {
  if (!agent?.live?.reachable && agent?.live?.status === 'unreachable') return 'Unreachable';
  return (
    agent?.live?.status ||
    agent?.live?.agent_status ||
    agent?.runtime?.status ||
    agent?.status ||
    'unknown'
  );
}

export function statusClass(status: string): string {
  const s = (status || '').toLowerCase();
  if (s.includes('alive') || s.includes('active') || s === 'healthy') return 'ok';
  if (s.includes('chat') || s.includes('awake')) return 'busy';
  if (s.includes('sleep')) return 'sleep';
  if (s.includes('unreach') || s.includes('offline') || s.includes('error')) return 'bad';
  return 'neutral';
}

export function hormoneEntries(hormones: any): { name: string; value: number }[] {
  if (!hormones || typeof hormones !== 'object') return [];
  const live = hormones;
  if (live.cortisol != null) {
    return Object.entries(live)
      .filter(([, v]) => typeof v === 'number')
      .map(([name, value]) => ({ name, value: value as number }));
  }
  const series = hormones.hormones ?? hormones;
  if (!series || typeof series !== 'object') return [];
  const out: { name: string; value: number }[] = [];
  for (const [name, points] of Object.entries(series)) {
    if (Array.isArray(points) && points.length) {
      const last = points[points.length - 1] as { value?: number };
      if (typeof last?.value === 'number') {
        out.push({ name, value: last.value });
      }
    }
  }
  return out.sort((a, b) => b.value - a.value);
}
