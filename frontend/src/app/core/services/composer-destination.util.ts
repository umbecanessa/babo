export interface ThreadMeta {
  key: string;
  label: string;
  channel: string;
  sender?: string;
  subject?: string;
}

export interface ComposerDestination {
  placeholder: string;
  hint: string;
  mode: 'private' | 'surface' | 'readonly';
  surface?: string;
}

export type ComposerTranslateFn = (
  key: string,
  params?: Record<string, string>,
) => string;

/** Honest composer labels — always state where a send goes. */
export function composerDestination(
  meta: ThreadMeta | null | undefined,
  isDefaultHome = false,
  t?: ComposerTranslateFn,
): ComposerDestination {
  const tr = (key: string, params?: Record<string, string>, fallback?: string) =>
    t ? t(key, params) : (fallback ?? key);

  if (!meta || meta.key === 'websocket:main' || isDefaultHome) {
    return {
      placeholder: tr(
        'chat.composer.privatePlaceholder',
        undefined,
        'Message Babo (private — not sent externally)',
      ),
      hint: '',
      mode: 'private',
    };
  }

  if (meta.channel === 'websocket') {
    return {
      placeholder: tr(
        'chat.composer.privateBranchPlaceholder',
        undefined,
        'Message Babo (private branch)',
      ),
      hint: '',
      mode: 'private',
    };
  }

  const surface = meta.channel;

  switch (surface) {
    case 'discord': {
      if (meta.key.includes(':dm:')) {
        const who = meta.sender ? `@${meta.sender}` : meta.label;
        return {
          placeholder: tr(
            'chat.composer.discordDm',
            { who },
            `Reply on Discord to ${who}`,
          ),
          hint: `Discord DM · ${who}`,
          mode: 'surface',
          surface: 'discord',
        };
      }
      const ch = meta.label.startsWith('#') ? meta.label : `#${meta.label}`;
      return {
        placeholder: tr(
          'chat.composer.discordChannel',
          { channel: ch },
          `Reply in ${ch} on Discord`,
        ),
        hint: `Discord · ${ch}`,
        mode: 'surface',
        surface: 'discord',
      };
    }
    case 'telegram': {
      if (meta.key.includes(':group:')) {
        return {
          placeholder: tr(
            'chat.composer.telegramGroup',
            { label: meta.label },
            `Reply in ${meta.label} on Telegram`,
          ),
          hint: `Telegram group · ${meta.label}`,
          mode: 'surface',
          surface: 'telegram',
        };
      }
      const who = meta.sender ? `@${meta.sender}` : meta.label;
      return {
        placeholder: tr(
          'chat.composer.telegramDm',
          { who },
          `Reply on Telegram to ${who}`,
        ),
        hint: `Telegram DM · ${who}`,
        mode: 'surface',
        surface: 'telegram',
      };
    }
    case 'whatsapp': {
      if (meta.key.includes(':group:')) {
        return {
          placeholder: tr(
            'chat.composer.whatsappGroup',
            { label: meta.label },
            `Reply in ${meta.label} on WhatsApp`,
          ),
          hint: `WhatsApp group · ${meta.label}`,
          mode: 'surface',
          surface: 'whatsapp',
        };
      }
      const who = meta.sender || meta.label;
      return {
        placeholder: tr(
          'chat.composer.whatsappDm',
          { who },
          `Reply on WhatsApp to ${who}`,
        ),
        hint: `WhatsApp · ${who}`,
        mode: 'surface',
        surface: 'whatsapp',
      };
    }
    case 'slack': {
      if (meta.key.includes(':dm:')) {
        return {
          placeholder: tr(
            'chat.composer.surfaceReply',
            { surface: 'Slack' },
            'Reply on Slack (DM)',
          ),
          hint: `Slack DM`,
          mode: 'surface',
          surface: 'slack',
        };
      }
      const ch = meta.label.startsWith('#') ? meta.label : meta.label;
      return {
        placeholder: tr(
          'chat.composer.discordChannel',
          { channel: ch },
          `Reply in ${ch} on Slack`,
        ).replace('Discord', 'Slack'),
        hint: `Slack · ${ch}`,
        mode: 'surface',
        surface: 'slack',
      };
    }
    case 'email': {
      const subj = meta.subject || meta.label || 'email thread';
      return {
        placeholder: tr(
          'chat.composer.emailReply',
          { label: subj },
          `Reply on email thread: ${subj}`,
        ),
        hint: `Email · ${subj}`,
        mode: 'surface',
        surface: 'email',
      };
    }
    default:
      return {
        placeholder: tr(
          'chat.composer.surfaceReply',
          { surface },
          `Reply via ${surface}`,
        ),
        hint: surface,
        mode: 'surface',
        surface,
      };
  }
}

/** Breadcrumb segments for the active conversation. */
export function conversationBreadcrumbs(
  meta: ThreadMeta | null | undefined,
  isDefaultHome = false,
): { label: string; level: string }[] {
  if (!meta || meta.key === 'websocket:main' || isDefaultHome) {
    return [{ label: 'Private desk', level: 'home' }];
  }

  if (meta.channel === 'websocket') {
    return [{ label: 'Home', level: 'home' }, { label: meta.label, level: 'branch' }];
  }

  const surfaceLabel = meta.channel.charAt(0).toUpperCase() + meta.channel.slice(1);
  const crumbs: { label: string; level: string }[] = [
    { label: surfaceLabel, level: 'surface' },
  ];

  if (meta.channel === 'email') {
    crumbs.push({ label: meta.subject || meta.label || 'Thread', level: 'conversation' });
    return crumbs;
  }

  if (meta.key.includes(':group:') || meta.key.includes(':channel:')) {
    crumbs.push({ label: meta.label, level: 'conversation' });
    return crumbs;
  }

  crumbs.push({ label: meta.sender ? `@${meta.sender}` : meta.label, level: 'conversation' });
  return crumbs;
}
