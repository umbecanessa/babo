import { TestBed } from '@angular/core/testing';
import { ConversationService } from './conversation.service';
import { ChatWorkbenchService } from './chat-workbench.service';

describe('ChatWorkbenchService', () => {
  let service: ChatWorkbenchService;
  let conversations: ConversationService;
  const branchKey = 'websocket:thread:abc';

  beforeEach(() => {
    TestBed.configureTestingModule({});
    service = TestBed.inject(ChatWorkbenchService);
    conversations = TestBed.inject(ConversationService);
    service.bindAgent('agent-a');
    service.setActiveSessionKey(branchKey);
  });

  it('tags tool rows with the active branch when runtime omits session_key', () => {
    service.recordFromRuntime({
      type: 'tool_execution_start',
      tool_name: 'read',
      call_id: 'call-1',
      arguments: { path: 'README.md' },
    });

    const entry = service.snapshotState().entries.find((e) => e.correlationKey === 'call-1');
    expect(entry?.sessionKey).toBe(branchKey);
  });

  it('removeEntriesForSession keeps untagged rows by default', () => {
    service.restoreState(false, [
      {
        id: '1',
        ts: 1,
        lane: 'chat',
        kind: 'tool',
        title: 'Read file',
        status: 'ok',
        toolLabel: 'Read',
        correlationKey: 'read-1',
      } as any,
      {
        id: '2',
        ts: 2,
        lane: 'chat',
        kind: 'agentic',
        title: 'Agent task started',
        status: 'running',
        toolLabel: 'Task',
        correlationKey: 'agentic',
        sessionKey: branchKey,
      } as any,
    ]);

    service.removeEntriesForSession(branchKey);

    const entries = service.snapshotState().entries;
    expect(entries.some((e) => !e.sessionKey)).toBe(true);
    expect(entries.some((e) => e.sessionKey === branchKey)).toBe(false);
  });

  it('removeEntriesForSession can clear untagged rows when deleting a branch', () => {
    service.restoreState(false, [{
      id: '1',
      ts: 1,
      lane: 'chat',
      kind: 'tool',
      title: 'Read file',
      status: 'ok',
      toolLabel: 'Read',
      correlationKey: 'read-1',
    } as any]);

    service.removeEntriesForSession(branchKey, { includeUntagged: true });

    expect(service.snapshotState().entries.length).toBe(0);
  });
});
