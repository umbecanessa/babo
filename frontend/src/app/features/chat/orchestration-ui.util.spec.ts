import {
  isUserFacingOrchestrationMessage,
  isSilentAutonomousCompletion,
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
