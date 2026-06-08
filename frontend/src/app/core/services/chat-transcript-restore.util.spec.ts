import {
  restoreChatMessagesFromTranscript,
  isChatSystemInjection,
  buildWorkbenchRestoreEntries,
  transcriptHasAgenticTrace,
  parseTranscriptUserContent,
  mergeTranscriptPreservingEphemeral,
} from './chat-transcript-restore.util';

describe('chat-transcript-restore.util', () => {
  it('restores agentic tool chips and prose without empty agentic rows', () => {
    const restored = restoreChatMessagesFromTranscript([
      {
        role: 'user',
        content: 'Build the feature',
        timestamp: 1_700_000_000,
      },
      {
        role: 'assistant',
        content: 'Done.',
        metadata: {
          agentic: true,
          iterations: 2,
          tool_calls: 3,
          aborted: true,
          abort_reason: 'Connection closed during task',
          events: [
            {
              step: 1,
              prose: 'Working on step 1.',
              tool_calls: [{
                name: 'read',
                call_id: 'call_1',
                arguments: { path: 'src/app.ts' },
              }],
              tool_results: [{ success: true, result_preview: 'file contents' }],
              duration_ms: 100,
            },
          ],
        },
      },
    ]);

    const types = restored.map(m => m.type);
    expect(types).not.toContain('agentic_start');
    expect(types).not.toContain('agentic_iteration');
    expect(types).not.toContain('agentic_complete');
    expect(types).toContain('tool_progress');
    expect(types).toContain('assistant');
    expect(restored.filter(m => m.type === 'assistant').length).toBe(2);
    expect(restored.find(m => m.type === 'tool_progress')?.toolProgress?.toolName).toBe('read');
    expect(restored.find(m => m.content === 'Done.')).toBeTruthy();
  });

  it('does not duplicate assistant prose when agentic events match row content', () => {
    const finalText = 'Great, the token worked! Your bot is connected.';
    const restored = restoreChatMessagesFromTranscript([
      { role: 'user', content: 'token here', timestamp: 1_700_000_000 },
      {
        role: 'assistant',
        content: finalText,
        metadata: {
          agentic: true,
          iterations: 1,
          tool_calls: 1,
          events: [{
            step: 1,
            prose: finalText,
            tool_calls: [{
              name: 'discord_setup',
              call_id: 'call_abc',
              arguments: { bot_token: 'x' },
            }],
            tool_results: [{ success: true }],
          }],
        },
      },
    ]);

    expect(restored.filter(m => m.type === 'assistant' && m.content === finalText)).toHaveLength(1);
    expect(restored.filter(m => m.type === 'tool_progress')).toHaveLength(1);
  });

  it('mergeTranscriptPreservingEphemeral keeps recent status pills', () => {
    const restored = restoreChatMessagesFromTranscript([
      { role: 'user', content: 'Connect discord', timestamp: 1_700_000_000 },
    ]);
    const now = new Date();
    const merged = mergeTranscriptPreservingEphemeral(restored, [{
      type: 'status',
      content: 'Starting discord setup...',
      timestamp: now,
    }]);
    expect(merged.some(m => m.content === 'Starting discord setup...')).toBe(true);
  });

  it('mergeTranscriptPreservingEphemeral skips trailing tool chips when agentic trace restored', () => {
    const restored = restoreChatMessagesFromTranscript([
      { role: 'user', content: 'Scan server', timestamp: 1_700_000_000 },
      {
        role: 'assistant',
        content: 'Scan complete.',
        metadata: {
          agentic: true,
          iterations: 1,
          tool_calls: 1,
          events: [{
            step: 1,
            prose: 'Scan complete.',
            tool_calls: [{
              name: 'read',
              call_id: 'call_live',
              arguments: { path: 'README.md' },
            }],
            tool_results: [{ success: true }],
          }],
        },
      },
    ]);
    const liveChip = {
      type: 'tool_progress' as const,
      content: 'Reading README.md…',
      timestamp: new Date(),
      toolProgress: {
        toolName: 'read',
        callId: 'call_live',
        done: true,
      },
    };
    const merged = mergeTranscriptPreservingEphemeral(
      restored,
      [liveChip as any, { type: 'status' as const, content: 'Still working…', timestamp: new Date() }],
      { skipToolProgress: true },
    );

    expect(merged.filter(m => m.type === 'tool_progress')).toHaveLength(1);
    expect(merged[merged.length - 1].type).toBe('status');
    expect(merged[merged.length - 1].content).toBe('Still working…');
  });

  it('filters system injection messages', () => {
    const restored = restoreChatMessagesFromTranscript([
      { role: 'user', content: '[REMEMBERED foo]' },
      { role: 'user', content: 'Hello' },
    ]);
    expect(restored).toHaveLength(1);
    expect(restored[0].content).toBe('Hello');
  });

  it('isChatSystemInjection detects internal markers', () => {
    expect(isChatSystemInjection('[REMEMBERED x]')).toBe(true);
    expect(isChatSystemInjection('Hello')).toBe(false);
  });

  it('buildWorkbenchRestoreEntries expands agentic events', () => {
    const rows = [
      {
        role: 'assistant',
        content: '',
        metadata: {
          agentic: true,
          iterations: 2,
          tool_calls: 1,
          events: [
            {
              step: 1,
              tool_calls: [{
                name: 'plan',
                call_id: 'call_1',
                arguments: { action: 'create', plan_id: 'plan_abc', title: 'My plan' },
              }],
              tool_results: [{ success: true }],
              duration_ms: 120,
            },
          ],
        },
      },
    ];
    expect(transcriptHasAgenticTrace(rows)).toBe(true);
    const entries = buildWorkbenchRestoreEntries(rows);
    expect(entries.some(e => e.kind === 'agentic' && e.title.includes('restored'))).toBe(true);
    expect(entries.some(e => e.kind === 'tool' && e.title === 'Create plan')).toBe(true);
  });

  it('parseTranscriptUserContent extracts attachment chips', () => {
    const parsed = parseTranscriptUserContent({
      role: 'user',
      content: `[The user attached 1 file(s):
  - prd.md (text, 22.7KB)
    read(path="C:\\\\data\\\\uploads\\\\prd.md")
Use the read tool with the EXACT path shown above.]

Build the platform`,
    });
    expect(parsed.content).toBe('Build the platform');
    expect(parsed.attachments?.length).toBe(1);
    expect(parsed.attachments?.[0].name).toBe('prd.md');
  });

  it('restores user rows with attachments only', () => {
    const restored = restoreChatMessagesFromTranscript([
      {
        role: 'user',
        content: '',
        attachments: [{ name: 'doc.pdf', path: '/tmp/doc.pdf', mime_type: 'application/pdf' }],
        timestamp: 1_700_000_000,
      },
    ]);
    expect(restored).toHaveLength(1);
    expect(restored[0].type).toBe('user');
    expect(restored[0].attachments?.[0].name).toBe('doc.pdf');
  });
});
