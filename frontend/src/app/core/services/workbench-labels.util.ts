/** Human-readable workbench titles for orchestration tools. */

const MODE_LABELS: Record<string, string> = {
  planning: 'Planning',
  delegating: 'Delegating',
  monitoring: 'Monitoring',
  evaluating: 'Evaluating',
  executing: 'Executing',
  responding: 'Responding',
};

export function formatAgentMode(mode: string): string {
  const key = (mode || '').toLowerCase().trim();
  return MODE_LABELS[key] || (key ? key.charAt(0).toUpperCase() + key.slice(1) : 'Unknown');
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
  opts?: { lastMode?: string },
): { title: string; subtitle?: string; toolLabel: string; modeTransition?: string } {
  const toolLabel = toolName || 'tool';

  if (toolName === 'switch_mode') {
    const to = str(args['mode'], 24).toLowerCase();
    const from = opts?.lastMode ? formatAgentMode(opts.lastMode) : '?';
    const toLabel = formatAgentMode(to);
    return {
      title: `${from} → ${toLabel}`,
      subtitle: str(args['reason'], 120) || undefined,
      toolLabel: 'Mode',
      modeTransition: to,
    };
  }

  if (toolName === 'plan') {
    const title = planAction(args);
    return { title, subtitle: str(args['title'], 60) || undefined, toolLabel: 'Plan' };
  }

  if (toolName === 'team') {
    const action = str(args['action'], 24).toLowerCase();
    const hintMsg = action === 'hint' ? str(args['message'], 120) : '';
    return {
      title: teamAction(args),
      subtitle: hintMsg || undefined,
      toolLabel: 'Team',
    };
  }

  if (toolName === 'todo') {
    const action = str(args['action'], 20) || 'list';
    const title = str(args['title'], 60);
    return {
      title: title ? `Todo: ${action} — ${title}` : `Todo: ${action}`,
      toolLabel: 'Todo',
    };
  }

  if (toolName === 'await_delegates') {
    return {
      title: 'Await delegates',
      subtitle: str(args['summary'], 100) || undefined,
      toolLabel: 'Orchestrator',
    };
  }

  if (toolName === 'write' || toolName === 'write_file' || toolName === 'create_file') {
    return { title: 'Write file', toolLabel: 'Write' };
  }

  if (toolName === 'edit') {
    return { title: 'Edit file', toolLabel: 'Edit' };
  }

  if (toolName === 'read' || toolName === 'read_file') {
    return { title: 'Read file', toolLabel: 'Read' };
  }

  if (toolName === 'delete_file') {
    return { title: 'Delete file', toolLabel: 'Delete' };
  }

  if (toolName === 'move_file') {
    return { title: 'Move file', toolLabel: 'Move' };
  }

  if (toolName === 'bash') {
    const cmd = str(args['command'], 88);
    return { title: cmd ? `$ ${cmd}` : 'Shell command', toolLabel: 'Bash' };
  }

  if (toolName === 'list_dir') {
    return { title: 'List folder', toolLabel: 'List' };
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
      toolLabel: 'Search',
    };
  }

  if (toolName === 'web_fetch') {
    return { title: `Fetch ${str(args['url'], 72)}`, toolLabel: 'Fetch' };
  }

  if (toolName === 'delegate') {
    return {
      title: `Delegate: ${str(args['task'], 72)}`,
      toolLabel: 'Delegate',
    };
  }

  if (toolName === 'communicate') {
    return {
      title: 'Stakeholder update',
      subtitle: str(args['message'] || args['status'], 100),
      toolLabel: 'Comms',
    };
  }

  return {
    title: toolName ? `${toolName}…` : 'Tool',
    toolLabel: toolName || 'Tool',
  };
}

export function toolWorkbenchEndTitle(
  toolName: string,
  isError: boolean,
  startTitle: string,
): string {
  if (isError) {
    return `${startTitle} — failed`;
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
