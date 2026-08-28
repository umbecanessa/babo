import { TestBed } from '@angular/core/testing';
import { ConversationService } from './conversation.service';

describe('ConversationService', () => {
  let service: ConversationService;

  beforeEach(() => {
    TestBed.configureTestingModule({});
    service = TestBed.inject(ConversationService);
    service.clearInbox();
  });

  it('starts with Home thread', () => {
    expect(service.threads().some(t => t.key === 'websocket:main')).toBeTrue();
  });

  it('upserts and groups threads by surface', () => {
    service.upsertThread({
      key: 'discord:channel:abc',
      label: '#general',
      channel: 'discord',
    });
    const groups = service.groupedThreads();
    expect(groups.some(g => g.channel === 'discord')).toBeTrue();
  });

  it('tracks inbox badge for high-priority unread items', () => {
    service.addInboxItem({
      sessionKey: 'discord:channel:abc',
      kind: 'inbound',
      preview: 'hello',
      channel: 'discord',
      timestamp: new Date(),
      priority: 60,
    });
    expect(service.inboxBadge()).toBe(1);
  });

  it('builds Discord labels from channel_name', () => {
    expect(service.buildThreadLabel('discord', { channel_name: 'general' })).toBe('#general');
  });

  it('resetThreadsForAgent replaces threads when switching agents', () => {
    service.resetThreadsForAgent('agent-a', [{
      key: 'telegram:group:-1001',
      label: 'Old Telegram',
      channel: 'telegram',
    }]);
    expect(service.threads().some(t => t.key === 'telegram:group:-1001')).toBeTrue();

    service.resetThreadsForAgent('agent-b', []);
    const keys = service.threads().map(t => t.key);
    expect(keys).toEqual(['websocket:main']);
    expect(keys).not.toContain('telegram:group:-1001');
  });

  it('resetThreadsForAgent keeps websocket branches on same-agent reload', () => {
    service.resetThreadsForAgent('agent-a', []);
    service.addBranch('Branch 1', 'websocket:thread:abc');
    service.resetThreadsForAgent('agent-a', [{
      key: 'discord:channel:1',
      label: '#general',
      channel: 'discord',
    }]);
    const keys = service.threads().map(t => t.key);
    expect(keys).toContain('websocket:main');
    expect(keys).toContain('websocket:thread:abc');
    expect(keys).toContain('discord:channel:1');
  });

  it('tracks default home for message filtering', () => {
    service.setDefaultHomeForAgent('agent-a', 'websocket:thread:home1');
    const msgs = [
      { sessionKey: 'websocket:thread:home1', content: 'a' },
      { sessionKey: 'websocket:thread:other', content: 'b' },
      { type: 'tool_progress', content: 'c' },
      { sessionKey: 'websocket:main', type: 'assistant', content: 'd' },
    ];
    expect(service.messagesForThread(msgs, 'websocket:thread:home1', 'agent-a').length).toBe(3);
    expect(service.homeMessages(msgs, 'agent-a').length).toBe(3);
  });

  it('includes untagged agentic rows on promoted home only', () => {
    const homeBranch = 'websocket:thread:home1';
    const otherBranch = 'websocket:thread:branch1';
    service.setDefaultHomeForAgent('agent-a', homeBranch);
    const msgs = [
      { sessionKey: homeBranch, type: 'tool_progress', content: 'bash' },
      { type: 'turn_thinking', content: 'planning' },
      { type: 'assistant', agenticStep: 2, content: 'next I will run tests' },
      { sessionKey: otherBranch, type: 'assistant', content: 'other branch' },
    ];
    const homeFiltered = service.messagesForThread(msgs, homeBranch, 'agent-a');
    expect(homeFiltered.length).toBe(3);
    expect(homeFiltered.some(m => m.type === 'turn_thinking')).toBeTrue();
    const branchFiltered = service.messagesForThread(msgs, otherBranch, 'agent-a');
    expect(branchFiltered.length).toBe(1);
    expect(branchFiltered[0].content).toBe('other branch');
  });

  it('maps legacy websocket:main workbench rows to promoted home', () => {
    service.setDefaultHomeForAgent('agent-a', 'websocket:thread:home1');
    expect(
      service.sessionBelongsToThread('websocket:main', 'websocket:thread:home1', 'agent-a'),
    ).toBeTrue();
    expect(
      service.sessionBelongsToThread('websocket:thread:other', 'websocket:thread:home1', 'agent-a'),
    ).toBeFalse();
  });

  it('resolveDeskSessionKey maps legacy tags to promoted home branch', () => {
    service.setDefaultHomeForAgent('agent-a', 'websocket:thread:home1');
    expect(
      service.resolveDeskSessionKey(undefined, 'websocket:thread:home1', 'agent-a'),
    ).toBe('websocket:thread:home1');
    expect(
      service.resolveDeskSessionKey('websocket:main', 'websocket:thread:home1', 'agent-a'),
    ).toBe('websocket:thread:home1');
  });

  it('resolveDeskSessionKey pins live desk traffic to the active branch', () => {
    const branch = 'websocket:thread:branch1';
    expect(
      service.resolveDeskSessionKey('websocket:thread:other', branch, 'agent-a'),
    ).toBe(branch);
    expect(
      service.resolveDeskSessionKey('websocket:main', branch, 'agent-a'),
    ).toBe(branch);
  });

  it('pinLegacyHomeTags moves websocket:main tool rows onto outgoing home branch', () => {
    service.setDefaultHomeForAgent('agent-a', 'websocket:thread:old-home');
    const rows = [
      { sessionKey: 'websocket:main', type: 'tool_progress', content: 'bash done' },
      { sessionKey: 'websocket:thread:other', type: 'assistant', content: 'other' },
    ];
    const pinned = service.pinLegacyHomeTags(rows, 'websocket:thread:old-home', 'agent-a');
    expect(pinned[0].sessionKey).toBe('websocket:thread:old-home');
    expect(pinned[1].sessionKey).toBe('websocket:thread:other');
  });
});
