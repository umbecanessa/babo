import {
  isUserFacingOrchestrationMessage,
  isSilentAutonomousCompletion,
  runtimeModeForYieldExit,
} from './orchestration-ui.util';

describe('isUserFacingOrchestrationMessage', () => {
  it('shows only explicit user_facing autonomous communicates', () => {
    expect(
      isUserFacingOrchestrationMessage({
        autonomous: true,
        user_facing: true,
        source: 'milestone:wave_complete:team_x',
      }),
    ).toBe(true);
    expect(
      isUserFacingOrchestrationMessage({
        autonomous: true,
        source: 'milestone:wave_complete:team_x',
      }),
    ).toBe(false);
  });
});

describe('isSilentAutonomousCompletion', () => {
  it('still silences routine orchestration completions', () => {
    expect(
      isSilentAutonomousCompletion({
        aborted: false,
        source: 'team_checkback:team_x',
      }),
    ).toBe(true);
  });
});

describe('runtimeModeForYieldExit', () => {
  it('maps wave launch yields to monitoring', () => {
    expect(runtimeModeForYieldExit('post_launch_yield')).toBe('monitoring');
    expect(runtimeModeForYieldExit('awaiting_delegates')).toBe('monitoring');
    expect(runtimeModeForYieldExit('task_complete')).toBeNull();
  });
});
