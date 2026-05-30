import { computeUpstreamCostCents, getModelPrice } from './model-prices';

describe('model-prices', () => {
  it('returns gemini flash pricing', () => {
    const p = getModelPrice('google/gemini-2.5-flash');
    expect(p.inputPerM).toBe(0.3);
    expect(p.outputPerM).toBe(2.5);
  });

  it('computes cents for token usage', () => {
    const cents = computeUpstreamCostCents(
      'google/gemini-2.5-flash',
      1_000_000,
      10_000,
    );
    expect(cents).toBeGreaterThan(0);
    expect(cents).toBeLessThan(50);
  });
});
