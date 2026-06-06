import { restoreChatMessagesFromTranscript, isChatSystemInjection } from './chat-transcript-restore.util';

describe('chat-transcript-restore.util', () => {
  it('expands agentic metadata into trace messages', () => {
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
              tool_calls: [{ name: 'read' }],
              tool_results: [{ success: true }],
              duration_ms: 100,
            },
          ],
        },
      },
    ]);

    const types = restored.map(m => m.type);
    expect(types).toContain('agentic_start');
    expect(types).toContain('agentic_iteration');
    expect(types).toContain('agentic_complete');
    expect(types).toContain('assistant');
    expect(restored.find(m => m.type === 'assistant')?.content).toBe('Done.');
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
});
