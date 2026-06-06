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
});
