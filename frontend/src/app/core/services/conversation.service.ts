import { Injectable, computed, signal } from '@angular/core';

export interface ConversationThread {
  key: string;
  label: string;
  channel: string;
  sender?: string;
  subject?: string;
  channelName?: string;
  isGroup?: boolean;
  unread?: boolean;
  guildName?: string;
}

export type InboxKind = 'unread' | 'ask_user' | 'skipped' | 'failed' | 'completed' | 'inbound' | 'outbound';

export interface InboxItem {
  id: string;
  sessionKey: string;
  kind: InboxKind;
  preview: string;
  sender?: string;
  channel: string;
  conversationLabel?: string;
  timestamp: Date;
  priority: number;
  read: boolean;
}

const SURFACE_ORDER = ['websocket', 'discord', 'telegram', 'whatsapp', 'slack', 'email'] as const;

const SURFACE_LABELS: Record<string, string> = {
  websocket: 'Home',
  discord: 'Discord',
  telegram: 'Telegram',
  whatsapp: 'WhatsApp',
  slack: 'Slack',
  email: 'Email',
};

/**
 * Conversation threads + inbox queue for multi-surface chat UX.
 */
@Injectable({ providedIn: 'root' })
export class ConversationService {
  private _activeAgentId: string | null = null;
  private readonly _threads = signal<ConversationThread[]>([
    { key: 'websocket:main', label: 'Home', channel: 'websocket' },
  ]);
  private readonly _inbox = signal<InboxItem[]>([]);

  readonly threads = this._threads.asReadonly();
  readonly inbox = this._inbox.asReadonly();

  readonly inboxBadge = computed(() =>
    this._inbox().filter((i) => !i.read && i.priority >= 50).length,
  );

  readonly groupedThreads = computed(() => {
    const threads = this._threads();
    const groups: { channel: string; label: string; threads: ConversationThread[] }[] = [];

    for (const ch of SURFACE_ORDER) {
      const chThreads = threads.filter((t) => t.channel === ch);
      if (chThreads.length === 0) continue;
      groups.push({
        channel: ch,
        label: SURFACE_LABELS[ch] || ch,
        threads: chThreads,
      });
    }

    const known = new Set<string>(SURFACE_ORDER);
    const extra = threads.filter((t) => !known.has(t.channel));
    if (extra.length > 0) {
      const byCh = new Map<string, ConversationThread[]>();
      for (const t of extra) {
        const arr = byCh.get(t.channel) || [];
        arr.push(t);
        byCh.set(t.channel, arr);
      }
      for (const [ch, chThreads] of byCh) {
        groups.push({ channel: ch, label: ch, threads: chThreads });
      }
    }
    return groups;
  });

  readonly inboxSections = computed(() => {
    const items = this._inbox().filter((i) => !i.read || i.kind === 'ask_user');
    const needsYou = items.filter((i) => i.kind === 'ask_user' || i.kind === 'failed');
    const unread = items.filter((i) => i.kind === 'unread' || i.kind === 'inbound');
    const skipped = items.filter((i) => i.kind === 'skipped');
    const recent = this._inbox()
      .filter((i) => i.kind === 'completed' || i.kind === 'outbound')
      .slice(0, 8);
    return { needsYou, unread, skipped, recent };
  });

  upsertThread(thread: ConversationThread): void {
    this._threads.update((list) => {
      const idx = list.findIndex((t) => t.key === thread.key);
      if (idx >= 0) {
        const next = [...list];
        next[idx] = { ...next[idx], ...thread };
        return next;
      }
      return [...list, thread];
    });
  }

  /**
   * Replace sidebar threads for an agent (Home + persisted sessions).
   * Clears cross-agent leakage from the root singleton service.
   * Preserves websocket branches when reloading the same agent.
   */
  resetThreadsForAgent(agentId: string, restored: ConversationThread[] = []): void {
    const home: ConversationThread = {
      key: 'websocket:main',
      label: 'Home',
      channel: 'websocket',
    };
    const sameAgent = agentId === this._activeAgentId;
    const branches = sameAgent
      ? this._threads().filter(
          (t) => t.channel === 'websocket' && t.key !== 'websocket:main',
        )
      : [];
    this._activeAgentId = agentId;

    const byKey = new Map<string, ConversationThread>();
    byKey.set(home.key, home);
    for (const b of branches) {
      byKey.set(b.key, b);
    }
    for (const r of restored) {
      if (r.key === 'websocket:main') continue;
      byKey.set(r.key, r);
    }
    this._threads.set(Array.from(byKey.values()));
    if (!sameAgent) {
      this._inbox.set([]);
    }
  }

  /** @deprecated Prefer resetThreadsForAgent — merge-only restore leaks across agents. */
  setThreadsFromRestore(restored: ConversationThread[]): void {
    if (restored.length === 0) return;
    this._threads.update((list) => {
      const byKey = new Map(list.map((t) => [t.key, t]));
      for (const r of restored) {
        const prev = byKey.get(r.key);
        if (!prev) {
          byKey.set(r.key, r);
          continue;
        }
        byKey.set(r.key, {
          ...prev,
          ...r,
          label: r.label || prev.label,
          channelName: r.channelName || prev.channelName,
          guildName: r.guildName || prev.guildName,
        });
      }
      return Array.from(byKey.values());
    });
  }

  /** True when sidebar metadata still looks like a raw platform id. */
  threadLabelLooksLikeId(thread: ConversationThread): boolean {
    const ident = thread.key.split(':')[2] || '';
    if (!ident) return false;
    const ch = (thread.channelName || '').replace(/^#/, '');
    return ch === ident
      || thread.label === ident
      || thread.label === `#${ident}`
      || thread.label.endsWith(` · ${ident.slice(-6)}`);
  }

  markThreadRead(sessionKey: string): void {
    this._threads.update((list) =>
      list.map((t) => (t.key === sessionKey ? { ...t, unread: false } : t)),
    );
    this._inbox.update((list) =>
      list.map((i) => (i.sessionKey === sessionKey ? { ...i, read: true } : i)),
    );
  }

  addInboxItem(partial: Omit<InboxItem, 'id' | 'read'> & { read?: boolean }): void {
    const item: InboxItem = {
      ...partial,
      id: `inbox-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      read: partial.read ?? false,
    };
    this._inbox.update((list) => [item, ...list].slice(0, 100));
    if (!item.read && item.priority >= 50) {
      this._threads.update((list) =>
        list.map((t) => (t.key === item.sessionKey ? { ...t, unread: true } : t)),
      );
    }
  }

  addBranch(label: string, key: string): ConversationThread {
    const thread: ConversationThread = { key, label, channel: 'websocket' };
    this.upsertThread(thread);
    return thread;
  }

  labelFromSessionKey(key: string, channel: string, meta?: {
    sender?: string;
    subject?: string;
    channel_name?: string;
    channelName?: string;
    guild_name?: string;
    guildName?: string;
  }): string {
    if (key === 'websocket:main') return 'Home';
    const parts = key.split(':');
    const threadType = parts[1] || '';
    const isGroup = threadType === 'group' || threadType === 'channel';
    const chName = (meta?.channelName || meta?.channel_name || '').replace(/^#/, '');
    const guild = meta?.guildName || meta?.guild_name || '';

    if (meta?.subject) {
      return meta.subject.replace(/^re:\s*/i, '').substring(0, 40);
    }
    switch (channel) {
      case 'discord':
        if (threadType === 'dm') return meta?.sender ? `@${meta.sender}` : (parts[2] || 'DM');
        if (guild && chName) return `${guild} · #${chName}`;
        if (chName) return `#${chName}`;
        return parts[2] ? `#${parts[2]}` : 'Channel';
      case 'telegram':
        if (isGroup) return chName || `Group · ${(parts[2] || '').slice(-6) || 'chat'}`;
        return meta?.sender ? `@${meta.sender}` : (parts[2] ? `@${parts[2]}` : 'Telegram DM');
      case 'whatsapp':
        if (isGroup) return chName || `Group · ${parts[2] || 'chat'}`;
        return meta?.sender || parts[2] || 'Chat';
      case 'slack':
        if (threadType === 'dm') return meta?.sender ? `@${meta.sender}` : (parts[2] || 'DM');
        if (guild && chName) return `${guild} · #${chName}`;
        if (chName) return `#${chName}`;
        return parts[2] || 'Channel';
      case 'email':
        return `Re: ${(parts.slice(2).join(':') || 'Thread').substring(0, 30)}`;
      default:
        if (meta?.sender && !isGroup) return meta.sender;
        return parts.slice(1).join(':') || 'Thread';
    }
  }

  buildThreadLabel(channel: string, event: {
    sender?: string;
    subject?: string;
    channel_name?: string;
    guild_name?: string;
    session_key?: string;
  }): string {
    const sender = event.sender || '';
    const sk = event.session_key || '';
    return this.labelFromSessionKey(sk, channel, {
      sender,
      subject: event.subject,
      channel_name: event.channel_name,
      guild_name: event.guild_name,
    });
  }

  threadFlagsFromKey(key: string): { isGroup: boolean; isDm: boolean } {
    const parts = key.split(':');
    const threadType = parts[1] || '';
    return {
      isGroup: threadType === 'group' || threadType === 'channel',
      isDm: threadType === 'dm',
    };
  }

  clearInbox(): void {
    this._inbox.set([]);
  }
}
