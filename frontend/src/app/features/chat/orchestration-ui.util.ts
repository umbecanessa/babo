/** Internal orchestrator loop exits — not user-facing failures. */
export const SILENT_ORCH_EXIT_REASONS = new Set([
  'post_launch_yield',
  'coordinator_burn',
  'monitor_iter_cap',
  'idle_monitor_yield',
  'idle_monitor',
  'awaiting_delegates',
  'checkback_suppressed',
  'wake_token_budget',
  'orchestration_preempted',
]);

const ORCHESTRATION_DISPATCH_PREFIXES = [
  'scheduler',
  'drive:',
  'delegate',
  'team_checkback:',
  'team_wave_complete:',
  'team_completion_review:',
  'check_back',
] as const;

/** Routine poll / check-back wakes — hide agentic_complete noise in chat. */
const ROUTINE_ORCHESTRATION_PREFIXES = [
  'scheduler',
  'drive:',
  'team_checkback:',
  'check_back',
] as const;

const ORCHESTRATION_DISPATCH_EXACT = new Set([
  'autonomous',
  'dmn',
  'idle',
  '',
  'delegate_batch_complete',
]);

export function isSilentOrchestrationExit(reason: string | undefined | null): boolean {
  const r = (reason || '').trim();
  return r !== '' && SILENT_ORCH_EXIT_REASONS.has(r);
}

/** Sub-agent delegate index from a runtime WS payload (0-based). */
export function delegateNumberFromMessage(msg: {
  sub_agent?: boolean;
  delegate_number?: number;
}): number | undefined {
  if (msg.sub_agent !== true) return undefined;
  const n = msg.delegate_number;
  return typeof n === 'number' && n >= 0 ? n : undefined;
}

/** User-facing label when the orchestrator yields after team launch / monitoring. */
export function orchestratorYieldLabel(reason: string | undefined | null): string {
  const r = (reason || '').trim();
  if (r === 'post_launch_yield') {
    return 'Wave launched — team is working';
  }
  if (r === 'awaiting_delegates') {
    return 'Monitoring sub-agents';
  }
  if (isSilentOrchestrationExit(r)) {
    return 'Coordinator check-in complete';
  }
  return '';
}

/** Scheduler / team check-back / wave-review wakes — not user chat content. */
export function isOrchestrationDispatchSource(source: string | undefined | null): boolean {
  const s = (source || '').trim();
  if (ORCHESTRATION_DISPATCH_EXACT.has(s)) {
    return true;
  }
  return ORCHESTRATION_DISPATCH_PREFIXES.some((p) => s.startsWith(p) || s === p);
}

/** Routine scheduler / check-back dispatches — not user-facing milestones. */
export function isRoutineOrchestrationDispatchSource(source: string | undefined | null): boolean {
  const s = (source || '').trim();
  if (s === 'autonomous' || s === 'dmn' || s === 'idle' || s === 'delegate_batch_complete') {
    return true;
  }
  return ROUTINE_ORCHESTRATION_PREFIXES.some((p) => s.startsWith(p) || s === p);
}

/**
 * Show autonomous communicate() in the main chat thread.
 * System wave milestones use user_facing only for meaningful transitions;
 * routine progress stays in workbench / project timeline.
 */
export function isUserFacingOrchestrationMessage(msg: {
  user_facing?: boolean;
  mid_loop?: boolean;
  source?: string;
}): boolean {
  return msg.user_facing === true;
}

/** Hide routine autonomous completions from the main chat thread. */
export function isSilentAutonomousCompletion(msg: {
  aborted?: boolean;
  abort_reason?: string;
  exit_reason?: string;
  source?: string;
}): boolean {
  if (isSilentOrchestrationExit(msg.abort_reason)) {
    return true;
  }
  if (isSilentOrchestrationExit(msg.exit_reason)) {
    return true;
  }
  // Scheduler / check-back only — wave-review wakes may still communicate().
  if (!msg.aborted && isRoutineOrchestrationDispatchSource(msg.source)) {
    return true;
  }
  return false;
}

export function agenticAbortLabel(
  aborted: boolean,
  abortReason?: string,
  autonomous?: boolean,
): string {
  const reason = (abortReason || '').trim();
  const yieldLabel = orchestratorYieldLabel(reason);
  if (yieldLabel) {
    return yieldLabel;
  }
  if (!aborted) {
    return autonomous ? 'Background task completed' : 'Task complete';
  }
  if (autonomous) {
    if (isSilentOrchestrationExit(reason)) {
      return '';
    }
    if (!reason || reason === 'user_abort' || reason === 'orchestration_preempted') {
      return 'Background check-in cancelled (system)';
    }
    return `Background task stopped: ${reason}`;
  }
  if (reason === 'user_abort') {
    return 'Stopped by user';
  }
  return `Task stopped: ${reason || 'Cancelled'}`;
}
