import {
  cleanToolResultPreview,
  collectFilePaths,
  extractPathFromToolContent,
  fileDisplayName,
  formatIterationToolSummary,
  normalizeToolArguments,
  normalizeWorkbenchFilePath,
  parseAgentMessageText,
  teamWorkbenchPresentation,
} from './activity-format.util';

describe('activity-format.util', () => {
  it('parses agent message routing tags', () => {
    const raw =
      '[AGENT_MSG|agent_id=abc12345|batch=deadbeef] '
      + '[TEAM CHECK-BACK — EM REVIEW] '
      + 'Team Wave 0 - Scaffolding [team_292889f8]\n\nScheduled check-in.';
    const r = parseAgentMessageText(raw);
    expect(r.headline).toContain('Wave 0');
    expect(r.chips.some((c) => c.label === 'Check-in')).toBe(true);
    expect(r.chips.some((c) => c.label === 'Team')).toBe(true);
  });

  it('strips todo boilerplate from previews', () => {
    const raw =
      'Added todo [9f18f31b]: Build platform ID (use this exact value for plan to do_id): 9f18f31b List: inbox | Priority: high';
    const cleaned = cleanToolResultPreview('todo', raw);
    expect(cleaned).not.toContain('use this exact value');
    expect(cleaned.toLowerCase()).toContain('build platform');
  });

  it('normalizes absolute agent paths to workspace-relative', () => {
    const abs =
      'C:\\Users\\umber\\AppData\\Roaming\\babo-desktop\\data\\agents\\c37c047d-e880-40ab-9f12-345678901234\\workspace\\icf-coaching\\README.md';
    expect(normalizeWorkbenchFilePath(abs)).toBe('icf-coaching/README.md');
  });

  it('collects file paths from read tool args', () => {
    const paths = collectFilePaths('read', {
      path: 'C:\\Users\\umber\\AppData\\Roaming\\babo-desktop\\data\\agents\\abc\\workspace\\docs\\prd.md',
    });
    expect(paths).toEqual(['docs/prd.md']);
  });

  it('unwraps vLLM input envelope for path extraction', () => {
    const paths = collectFilePaths('read', {
      input: JSON.stringify({
        path: 'C:\\Users\\x\\AppData\\Roaming\\babo-desktop\\data\\agents\\c37c047d-e880-40ab-9f12-345678901234\\workspace\\readme.md',
      }),
    });
    expect(paths).toEqual(['readme.md']);
  });

  it('extracts path from Reading label in chat content', () => {
    const abs =
      'Reading C:\\Users\\u\\AppData\\Roaming\\babo-desktop\\data\\agents\\c37c047d-e880-40ab-9f12-345678901234\\workspace\\icf-coaching\\PRD.md…';
    expect(extractPathFromToolContent(abs)).toBe('icf-coaching/PRD.md');
    expect(fileDisplayName(abs)).toBe('PRD.md');
  });

  it('collects relative paths from write success previews', () => {
    const preview =
      'Successfully wrote 95 lines (2841 bytes) to frontend/src/store/useStore.ts. '
      + 'Future changes to this file should use edit().';
    const paths = collectFilePaths('write', undefined, preview);
    expect(paths).toEqual(['frontend/src/store/useStore.ts']);
  });

  it('builds iteration summary from cached tool meta', () => {
    const meta = new Map<string, { title: string }>([
      ['c1', { title: 'Delegating → Monitoring' }],
    ]);
    const summary = formatIterationToolSummary(
      [{ name: 'switch_mode', call_id: 'c1' }],
      [{ success: true }],
      meta,
      'delegating',
    );
    expect(summary).toContain('Delegating → Monitoring');
    expect(summary).toContain('1/1 ok');
  });

  it('team hint uses routing chips and a single message block', () => {
    const pres = teamWorkbenchPresentation(
      {
        action: 'hint',
        team_id: 'team_95c4fabe',
        member: 2,
        message: 'Wrap up and verify the repo.',
      },
      'Hint delivered to member 2',
    );
    expect(pres.title).toBe('Team hint');
    expect(pres.subtitle).toBeUndefined();
    expect(pres.chips.some((c) => c.label === 'From' && c.value === 'Orchestrator')).toBe(
      true,
    );
    expect(pres.chips.some((c) => c.label === 'To' && c.value === 'Sub #2')).toBe(true);
    const block = pres.chips.find((c) => c.variant === 'block');
    expect(block?.value).toContain('Wrap up');
  });

  it('builds switch_mode summary from turn_end arguments when cache is empty', () => {
    const summary = formatIterationToolSummary(
      [
        {
          name: 'switch_mode',
          call_id: 'c2',
          arguments: { mode: 'planning' },
        },
      ],
      [{ success: true }],
      new Map(),
      'executing',
    );
    expect(summary).toContain('Executing → Planning');
    expect(summary).toContain('1/1 ok');
  });
});
