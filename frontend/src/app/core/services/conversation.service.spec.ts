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
});
