/**
 * Runtime chart / canvas colors — hex values aligned with punk-records CSS tokens.
 * Use for SVG, canvas, and inline styles where CSS variables are awkward.
 */
export const THEME_COLORS = {
  primary: '#b85c1a',
  secondary: '#2a7a72',
  success: '#2a7a72',
  warn: '#e5a520',
  danger: '#c0392b',
  gold: '#e5a520',
  text: '#e8eaf2',
  textMuted: '#6f7591',
  chart: ['#b85c1a', '#2a7a72', '#e5a520', '#c0392b', '#c97a3d', '#fb923c', '#6ee7b7'] as const,
  hormones: {
    dopamine: '#2a7a72',
    serotonin: '#b85c1a',
    norepinephrine: '#e5a520',
    cortisol: '#c0392b',
    oxytocin: '#ec4899',
  },
  network: {
    ecn: '#b85c1a',
    sn: '#e5a520',
    dmn: '#c97a3d',
  },
  memory: {
    user: '#b85c1a',
    agent: '#2a7a72',
    world: '#e5a520',
    system: '#c97a3d',
  },
  signals: {
    recall: '#b85c1a',
    lookup: '#b85c1a',
    bond: '#b85c1a',
  },
} as const;
