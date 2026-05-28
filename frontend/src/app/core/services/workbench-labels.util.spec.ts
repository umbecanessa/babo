import { toolWorkbenchTitle } from './workbench-labels.util';

describe('workbench-labels.util', () => {
  it('formats switch_mode as from → to', () => {
    const r = toolWorkbenchTitle('switch_mode', { mode: 'monitoring' }, {
      lastMode: 'delegating',
    });
    expect(r.title).toBe('Delegating → Monitoring');
    expect(r.toolLabel).toBe('Mode');
  });

  it('formats plan fix_dependencies', () => {
    const r = toolWorkbenchTitle('plan', {
      action: 'fix_dependencies',
      plan_id: 'plan_abc',
    });
    expect(r.title).toContain('fix dependencies');
    expect(r.toolLabel).toBe('Plan');
  });

  it('formats team launch', () => {
    const r = toolWorkbenchTitle('team', {
      action: 'launch',
      team_id: 'team_xyz',
    });
    expect(r.title).toContain('launch');
    expect(r.toolLabel).toBe('Team');
  });
});
