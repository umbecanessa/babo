export type TranslateFn = (key: string, params?: Record<string, unknown>) => string;

/** Human-readable workbench titles for orchestration tools. */

const MODE_LABELS: Record<string, string> = {
  planning: 'Planning',
  delegating: 'Delegating',
  monitoring: 'Monitoring',
  evaluating: 'Evaluating',
  executing: 'Executing',
  responding: 'Responding',
};

export function formatAgentMode(mode: string, t?: TranslateFn): string {
  const key = (mode || '').toLowerCase().trim();
  const i18nKey = `chat.workbench.labels.mode.${key}`;
  if (t) {
    const translated = t(i18nKey);
    if (translated !== i18nKey) return translated;
  }
  return MODE_LABELS[key] || (key ? key.charAt(0).toUpperCase() + key.slice(1) : (t ? t('chat.workbench.labels.mode.unknown') : 'Unknown'));
}


function L(key: string, fallback: string, t?: TranslateFn, params?: Record<string, unknown>): string {
  return t ? t(key, params) : fallback;
}

function str(v: unknown, max = 80): string {
  const s = v == null ? '' : String(v).trim();
  return s.length > max ? `${s.slice(0, max)}…` : s;
}

function planAction(args: Record<string, unknown>): string {
  const action = str(args['action'], 40) || 'read';
  const stepId = str(args['step_id'], 24);
  const planId = str(args['plan_id'], 20);
  switch (action) {
    case 'create':
      return `Plan: create${planId ? ` → ${planId}` : ''}`;
    case 'delete':
      return `Plan: archive${planId ? ` ${planId}` : ''}`;
    case 'fix_dependencies':
      return `Plan: fix dependencies${planId ? ` (${planId})` : ''}`;
    case 'continue_work':
      return 'Plan: continue work';
    case 'accept_partial':
      return `Plan: accept partial${stepId ? ` ${stepId}` : ''}`;
    case 'update': {
      const status = str(args['status'], 16);
      const deps = args['depends_on'];
      if (Array.isArray(deps) && deps.length) {
        return `Plan: set deps on ${stepId || 'step'}`;
      }
      return `Plan: update ${stepId || 'step'}${status ? ` → ${status}` : ''}`;
    }
    case 'complete':
      return 'Plan: complete';
    case 'read':
      return `Plan: read${planId ? ` ${planId}` : ''}`;
    default:
      return `Plan: ${action}`;
  }
}

function teamAction(args: Record<string, unknown>): string {
  const action = str(args['action'], 24) || 'inspect';
  const teamId = str(args['team_id'], 18);
  const wave = args['wave'];
  switch (action) {
    case 'create':
      return `Team: create${wave != null ? ` wave ${Number(wave) + 1}` : ''}`;
    case 'launch':
      return `Team: launch${teamId ? ` ${teamId}` : ''}`;
    case 'advance':
      return `Team: advance wave`;
    case 'inspect':
      return `Team: inspect${teamId ? ` ${teamId}` : ''}`;
    case 'intervene': {
      const decision = str(args['decision'], 16);
      const member = args['member'];
      return `Team: ${decision || 'intervene'}${member != null ? ` #${member}` : ''}`;
    }
    case 'hint': {
      const member = args['member'];
      return member != null ? `Team hint · #${member}` : 'Team hint';
    }
    case 'disband':
      return `Team: disband`;
    default:
      return `Team: ${action}`;
  }
}

export function toolWorkbenchTitle(
  toolName: string,
  args: Record<string, unknown>,
  opts?: { lastMode?: string; t?: TranslateFn },
): { title: string; subtitle?: string; toolLabel: string; modeTransition?: string } {
  const toolLabel = toolName || 'tool';

  if (toolName === 'switch_mode') {
    const to = str(args['mode'], 24).toLowerCase();
    const from = opts?.lastMode ? formatAgentMode(opts.lastMode, opts?.t) : '?';
    const toLabel = formatAgentMode(to, opts?.t);
    return {
      title: `${from} → ${toLabel}`,
      subtitle: str(args['reason'], 120) || undefined,
      toolLabel: L('chat.workbench.labels.modeLabel', 'Mode', opts?.t),
      modeTransition: to,
    };
  }

  if (toolName === 'plan') {
    const title = planAction(args);
    return { title, subtitle: str(args['title'], 60) || undefined, toolLabel: L('chat.workbench.labels.planLabel', 'Plan', opts?.t) };
  }

  if (toolName === 'team') {
    const action = str(args['action'], 24).toLowerCase();
    const hintMsg = action === 'hint' ? str(args['message'], 120) : '';
    return {
      title: teamAction(args),
      subtitle: hintMsg || undefined,
      toolLabel: L('chat.workbench.labels.teamLabel', 'Team', opts?.t),
    };
  }

  if (toolName === 'todo') {
    const action = str(args['action'], 20) || 'list';
    const title = str(args['title'], 60);
    return {
      title: title ? `Todo: ${action} — ${title}` : `Todo: ${action}`,
      toolLabel: L('chat.workbench.labels.todoLabel', 'Todo', opts?.t),
    };
  }

  if (toolName === 'await_delegates') {
    return {
      title: L('chat.workbench.labels.awaitDelegates', 'Await delegates', opts?.t),
      subtitle: str(args['summary'], 100) || undefined,
      toolLabel: L('chat.workbench.labels.orchestrator', 'Orchestrator', opts?.t),
    };
  }

  if (toolName === 'write' || toolName === 'write_file' || toolName === 'create_file') {
    return { title: L('chat.workbench.labels.writeFile', 'Write file', opts?.t), toolLabel: L('chat.workbench.labels.write', 'Write', opts?.t) };
  }

  if (toolName === 'edit') {
    return { title: L('chat.workbench.labels.editFile', 'Edit file', opts?.t), toolLabel: L('chat.workbench.labels.edit', 'Edit', opts?.t) };
  }

  if (toolName === 'read' || toolName === 'read_file') {
    return { title: L('chat.workbench.labels.readFile', 'Read file', opts?.t), toolLabel: L('chat.workbench.labels.read', 'Read', opts?.t) };
  }

  if (toolName === 'delete_file') {
    return { title: L('chat.workbench.labels.deleteFile', 'Delete file', opts?.t), toolLabel: L('chat.workbench.labels.delete', 'Delete', opts?.t) };
  }

  if (toolName === 'move_file') {
    return { title: L('chat.workbench.labels.moveFile', 'Move file', opts?.t), toolLabel: L('chat.workbench.labels.move', 'Move', opts?.t) };
  }

  if (toolName === 'bash') {
    const cmd = str(args['command'], 88);
    return { title: cmd ? `$ ${cmd}` : L('chat.workbench.labels.shellCommand', 'Shell command', opts?.t), toolLabel: L('chat.workbench.labels.bash', 'Bash', opts?.t) };
  }

  if (toolName === 'list_dir') {
    return { title: L('chat.workbench.labels.listFolder', 'List folder', opts?.t), toolLabel: L('chat.workbench.labels.list', 'List', opts?.t) };
  }

  if (toolName === 'glob' || toolName === 'grep') {
    return {
      title: toolName === 'glob' ? 'Find files' : 'Search in files',
      subtitle: str(args['pattern'] || args['path'], 72) || undefined,
      toolLabel: toolName === 'glob' ? 'Glob' : 'Grep',
    };
  }

  if (toolName === 'web_search' || toolName === 'search') {
    return {
      title: `Search: ${str(args['query'] || args['term'], 88)}`,
      toolLabel: L('chat.workbench.labels.search', 'Search', opts?.t),
    };
  }

  if (toolName === 'web_fetch') {
    return { title: `Fetch ${str(args['url'], 72)}`, toolLabel: L('chat.workbench.labels.fetch', 'Fetch', opts?.t) };
  }

  if (toolName === 'delegate') {
    return {
      title: `Delegate: ${str(args['task'], 72)}`,
      toolLabel: L('chat.workbench.labels.delegate', 'Delegate', opts?.t),
    };
  }

  if (toolName === 'communicate') {
    return {
      title: L('chat.workbench.labels.stakeholderUpdate', 'Stakeholder update', opts?.t),
      subtitle: str(args['message'] || args['status'], 100),
      toolLabel: L('chat.workbench.labels.comms', 'Comms', opts?.t),
    };
  }

  return {
    title: toolName ? `${toolName}…` : 'Tool',
    toolLabel: toolName || L('chat.workbench.labels.tool', 'Tool', opts?.t),
  };
}

export function toolWorkbenchEndTitle(
  toolName: string,
  isError: boolean,
  startTitle: string,
  isWarn = false,
): string {
  if (isError) {
    return `${startTitle} — failed`;
  }
  if (isWarn) {
    return startTitle;
  }
  if (
    toolName === 'switch_mode'
    || toolName === 'plan'
    || toolName === 'team'
    || toolName === 'todo'
    || toolName === 'read'
    || toolName === 'read_file'
    || toolName === 'write'
    || toolName === 'write_file'
    || toolName === 'edit'
    || toolName === 'await_delegates'
    || toolName === 'communicate'
  ) {
    return startTitle;
  }
  return `${startTitle} — done`;
}
