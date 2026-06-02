/**
 * Runtime chart / canvas colors — hex values aligned with punk-records CSS tokens.
 * Use for SVG, canvas, and inline styles where CSS variables are awkward.
 */
export const THEME_COLORS = {
  primary: '#7c5bf5',
  secondary: '#14b8a6',
  success: '#14b8a6',
  warn: '#e5a520',
  danger: '#c0392b',
  gold: '#e5a520',
  text: '#e8eaf2',
  textMuted: '#6f7591',
  chart: ['#7c5bf5', '#14b8a6', '#e5a520', '#c0392b', '#a78bfa', '#fb923c', '#6ee7b7'] as const,
  hormones: {
    dopamine: '#14b8a6',
    serotonin: '#7c5bf5',
    norepinephrine: '#e5a520',
    cortisol: '#c0392b',
    oxytocin: '#ec4899',
  },
  network: {
    ecn: '#7c5bf5',
    sn: '#e5a520',
    dmn: '#a78bfa',
  },
  memory: {
    user: '#7c5bf5',
    agent: '#14b8a6',
    world: '#e5a520',
    system: '#a78bfa',
  },
  signals: {
    recall: '#7c5bf5',
    lookup: '#7c5bf5',
    bond: '#7c5bf5',
  },
} as const;
