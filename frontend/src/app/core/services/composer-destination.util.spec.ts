import { composerDestination, conversationBreadcrumbs } from './composer-destination.util';

describe('composerDestination', () => {
  it('labels private home desk', () => {
    const dest = composerDestination({ key: 'websocket:main', label: 'Home', channel: 'websocket' });
    expect(dest.mode).toBe('private');
    expect(dest.placeholder).toContain('private');
  });

  it('labels Discord channel replies', () => {
    const dest = composerDestination({
      key: 'discord:channel:general',
      label: '#general',
      channel: 'discord',
    });
    expect(dest.mode).toBe('surface');
    expect(dest.surface).toBe('discord');
    expect(dest.placeholder).toContain('#general');
  });

  it('labels Discord DM replies', () => {
    const dest = composerDestination({
      key: 'discord:dm:123',
      label: 'alice',
      channel: 'discord',
      sender: 'alice',
    });
    expect(dest.placeholder).toContain('@alice');
  });
});

describe('conversationBreadcrumbs', () => {
  it('returns Home for main thread', () => {
    expect(conversationBreadcrumbs({ key: 'websocket:main', label: 'Home', channel: 'websocket' }))
      .toEqual([{ label: 'Private desk', level: 'home' }]);
  });

  it('includes surface for Discord channel', () => {
    const crumbs = conversationBreadcrumbs({
      key: 'discord:channel:general',
      label: '#general',
      channel: 'discord',
    });
    expect(crumbs[0].label).toBe('Discord');
    expect(crumbs[1].label).toBe('#general');
  });
});
