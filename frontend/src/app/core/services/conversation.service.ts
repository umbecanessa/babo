import { Injectable, computed, signal } from '@angular/core';

export interface ConversationThread {
  key: string;
  label: string;
  channel: string;
  sender?: string;
  subject?: string;
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

  setThreadsFromRestore(restored: ConversationThread[]): void {
    if (restored.length === 0) return;
    this._threads.update((list) => {
      const keys = new Set(list.map((t) => t.key));
      const merged = [...list];
      for (const r of restored) {
        if (!keys.has(r.key)) {
          merged.push(r);
          keys.add(r.key);
        }
      }
      return merged;
    });
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

  labelFromSessionKey(key: string, channel: string, meta?: { sender?: string; subject?: string }): string {
    if (key === 'websocket:main') return 'Home';
    const parts = key.split(':');
    if (meta?.subject) {
      return meta.subject.replace(/^re:\s*/i, '').substring(0, 40);
    }
    if (meta?.sender) {
      return channel === 'telegram' ? `@${meta.sender}` : meta.sender;
    }
    switch (channel) {
      case 'discord':
        return parts[1] === 'dm' ? (parts[2] || 'DM') : (parts[2] ? `#${parts[2]}` : 'Channel');
      case 'telegram':
        return parts[1] === 'group' ? (parts[2] || 'Group') : (parts[2] ? `@${parts[2]}` : 'DM');
      case 'whatsapp':
        return parts[2] || 'Chat';
      case 'slack':
        return parts[1] === 'dm' ? (parts[2] || 'DM') : (parts[2] || 'Channel');
      case 'email':
        return `Re: ${(parts.slice(2).join(':') || 'Thread').substring(0, 30)}`;
      default:
        return parts.slice(1).join(':') || 'Thread';
    }
  }

  buildThreadLabel(channel: string, event: {
    sender?: string;
    subject?: string;
    channel_name?: string;
  }): string {
    const sender = event.sender || '';
    switch (channel) {
      case 'telegram':
        return sender ? `@${sender}` : 'DM';
      case 'whatsapp':
        return sender || 'Chat';
      case 'email': {
        const subj = event.subject || '';
        return subj ? `Re: ${subj.replace(/^re:\s*/i, '').substring(0, 40)}` : sender || 'Thread';
      }
      case 'discord': {
        const ch = event.channel_name || '';
        return ch ? `#${ch}` : sender ? `@${sender}` : 'Discord';
      }
      case 'slack': {
        const ch = event.channel_name || '';
        return ch ? `#${ch}` : sender || 'Slack';
      }
      default:
        return sender || 'Thread';
    }
  }

  clearInbox(): void {
    this._inbox.set([]);
  }
}
