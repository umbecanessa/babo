import { Injectable, inject, signal, computed } from '@angular/core';
import {
  delegateNumberFromMessage,
  isSilentAutonomousCompletion,
  isSilentOrchestrationExit,
  orchestratorYieldLabel,
} from '../../features/chat/orchestration-ui.util';
import { enrichWorkspaceRelativePath } from '../../features/projects/workspace/workspace-path.util';
import { AgentWorkspaceContextService } from './agent-workspace-context.service';
import {
  cleanToolResultPreview,
  collectFilePaths,
  normalizeToolArguments,
  parseAgentMessageText,
  previewToChips,
  stripPathFromPreview,
  planWorkbenchPresentation,
  teamWorkbenchPresentation,
  type ActivityChip,
} from './activity-format.util';
import {
  formatAgentMode,
  toolWorkbenchEndTitle,
  toolWorkbenchTitle,
} from './workbench-labels.util';

import {
  filterWorkbenchEntries,
  type WorkbenchDensity,
} from './workbench-density.util';

export type WorkbenchEntryKind = 'agentic' | 'tool' | 'activity';

/** Chat thread task vs autonomous / memory-background work */
export type WorkbenchLane = 'chat' | 'background';

export interface WorkbenchEntry {
  id: string;
  ts: number;
  kind: WorkbenchEntryKind;
  lane: WorkbenchLane;
  title: string;
  subtitle?: string;
  status?: 'running' | 'ok' | 'error';
  correlationKey?: string;
  detail?: string;
  delegateNumber?: number;
  /** Short badge in the left column (Plan, Team, Mode, Bash, …) */
  toolLabel?: string;
  /** Active agent mode after switch_mode */
  mode?: string;
  /** Batch parallel tools from the same agentic iteration */
  groupKey?: string;
  /** Open in Projects → Files (first path, backward compat) */
  filePath?: string;
  /** Clickable file chips (read/write/edit/list_dir, …) */
  filePaths?: string[];
  chips?: ActivityChip[];
}

const MAX_ENTRIES = 250;
const OUTPUT_CAP = 10000;
const DETAIL_KEEP = 6000;

function newId(): string {
  return `wb-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

/**
 * Bottom workbench log: agentic steps, tool runs, activity lines.
 * Scoped per agent via bindAgent().
 */
@Injectable({ providedIn: 'root' })
export class ChatWorkbenchService {
  private readonly workspaceCtx = inject(AgentWorkspaceContextService);
  private readonly _agentId = signal<string>('');

  readonly panelOpen = signal(false);
  readonly entries = signal<WorkbenchEntry[]>([]);
  readonly density = signal<WorkbenchDensity>('focused');

  readonly entryCount = computed(() => this.entries().length);

  readonly filteredEntries = computed(() =>
    filterWorkbenchEntries(this.entries(), this.density()),
  );

  private _streamOutputKey: string | null = null;
  private _streamLane: WorkbenchLane = 'chat';
  private readonly _outputBuffers = new Map<string, string>();
  private _lastAgentMode = 'executing';
  private readonly _toolStartTitles = new Map<string, string>();
  private readonly _toolMetaByCallId = new Map<string, { title: string; subtitle?: string }>();
  /** Pending step batch: tag tool rows when their call_id completes. */
  private _pendingStepGroup: {
    groupKey: string;
    callIds: Set<string>;
    delegateNumber?: number;
  } | null = null;

  private _enrichPaths(paths: string[]): string[] {
    const pd = this.workspaceCtx.getProjectDir(this._agentId());
    if (!pd) return paths;
    return paths.map((p) => enrichWorkspaceRelativePath(p, pd));
  }

  /** Legacy titles prefixed with [Sub #N]; strip for display (chip shows delegate). */
  private _stripSubAgentTitlePrefix(title: string): string {
    return (title || '').replace(/^\[Sub(?:\s+#\d+)?\]\s*/i, '').trim();
  }

  bindAgent(agentId: string): void {
    if (this._agentId() === agentId) return;
    this._agentId.set(agentId);
    this.entries.set([]);
    this._resetStreamScratch();
    this._lastAgentMode = 'executing';
    this._toolStartTitles.clear();
    this._toolMetaByCallId.clear();
    this._pendingStepGroup = null;
  }

  openPanel(): void {
    this.panelOpen.set(true);
    this._focusKey.set(null);
  }

  closePanel(): void {
    this.panelOpen.set(false);
    this._focusKey.set(null);
  }

  togglePanel(): void {
    this.panelOpen.update((o) => {
      const next = !o;
      if (!next) {
        this._focusKey.set(null);
      }
      return next;
    });
  }

  setDensity(level: WorkbenchDensity): void {
    this.density.set(level);
  }

  cycleDensity(): void {
    const order: WorkbenchDensity[] = ['focused', 'standard', 'debug'];
    const idx = order.indexOf(this.density());
    this.density.set(order[(idx + 1) % order.length]);
  }

  clear(): void {
    this.entries.set([]);
    this._resetStreamScratch();
    this._toolStartTitles.clear();
    this._toolMetaByCallId.clear();
    this._pendingStepGroup = null;
  }

  restoreState(open: boolean, list: WorkbenchEntry[], density?: WorkbenchDensity): void {
    this._resetStreamScratch();
    this.panelOpen.set(open);
    if (density) {
      this.density.set(density);
    }
    this.entries.set(
      Array.isArray(list)
        ? list
            .filter((e) => e && e.id && e.title)
            .map((e): WorkbenchEntry => ({
              ...e,
              lane: e.lane === 'background' ? 'background' : 'chat',
            }))
            .slice(-MAX_ENTRIES)
        : [],
    );
  }

  snapshotState(): { open: boolean; entries: WorkbenchEntry[]; density: WorkbenchDensity } {
    return {
      open: this.panelOpen(),
      entries: this.entries().map((e) => ({ ...e })),
      density: this.density(),
    };
  }

  focusCorrelation(key: string): void {
    if (!key) return;
    this.panelOpen.set(true);
    this._focusKey.set(key);
  }

  private readonly _focusKey = signal<string | null>(null);
  readonly focusKey = this._focusKey.asReadonly();

  private _resetStreamScratch(): void {
    this._streamOutputKey = null;
    this._outputBuffers.clear();
  }

  private _corrMatchesCallId(correlationKey: string | undefined, callId: string): boolean {
    if (!correlationKey || !callId) return false;
    return correlationKey === callId || correlationKey.endsWith(callId);
  }

  private _applyGroupKeyToEntry(
    entry: WorkbenchEntry,
    groupKey: string,
    delegateNumber?: number,
  ): WorkbenchEntry | null {
    if (entry.kind !== 'tool' || entry.groupKey) return null;
    if (delegateNumber !== undefined) {
      if (entry.delegateNumber !== delegateNumber) return null;
    } else if (entry.delegateNumber) {
      return null;
    }
    return { ...entry, groupKey };
  }

  private _tagToolsForIteration(
    groupKey: string,
    toolCalls: Array<{ call_id?: string }>,
    delegateNumber?: number,
  ): void {
    const callIds = new Set(
      toolCalls.map((tc) => tc.call_id).filter((id): id is string => !!id),
    );
    if (!callIds.size) return;

    this.entries.update((list) => {
      let changed = false;
      const copy = list.map((e) => {
        const matchId = [...callIds].find((id) =>
          this._corrMatchesCallId(e.correlationKey, id),
        );
        if (!matchId) return e;
        const next = this._applyGroupKeyToEntry(e, groupKey, delegateNumber);
        if (!next) return e;
        changed = true;
        callIds.delete(matchId);
        return next;
      });
      return changed ? copy : list;
    });

    if (callIds.size > 0) {
      this._pendingStepGroup = { groupKey, callIds, delegateNumber };
    } else {
      this._pendingStepGroup = null;
    }
  }

  private _maybeTagPendingToolEnd(callId: string, delegateNumber?: number): void {
    const pending = this._pendingStepGroup;
    if (!pending || !callId || !pending.callIds.has(callId)) return;
    if (
      pending.delegateNumber !== undefined
      && pending.delegateNumber !== delegateNumber
    ) {
      return;
    }

    this.entries.update((list) => {
      let changed = false;
      const copy = list.map((e) => {
        if (!this._corrMatchesCallId(e.correlationKey, callId)) return e;
        const next = this._applyGroupKeyToEntry(
          e,
          pending.groupKey,
          pending.delegateNumber,
        );
        if (!next) return e;
        changed = true;
        return next;
      });
      return changed ? copy : list;
    });

    pending.callIds.delete(callId);
    if (pending.callIds.size === 0) {
      this._pendingStepGroup = null;
    }
  }

  private _streamCorr(): string | null {
    return this._streamOutputKey ? `out-${this._streamOutputKey}` : null;
  }

  private _mergeStreamOutput(toolName: string, chunk: string): void {
    if (!this._streamOutputKey) {
      this._streamOutputKey = `orphan-${toolName}-${Date.now()}`;
    }
    const corr = this._streamCorr()!;
    let acc = (this._outputBuffers.get(corr) || '') + chunk;
    if (acc.length > OUTPUT_CAP) {
      acc = acc.slice(-OUTPUT_CAP);
    }
    this._outputBuffers.set(corr, acc);
    const tail = acc.slice(-320).trim();
    const detail = acc.slice(-DETAIL_KEEP);

    this._upsert(corr, {
      lane: this._streamLane,
      kind: 'tool',
      title: `Shell output`,
      subtitle: tail || '(streaming…)',
      detail,
      status: 'running',
      toolLabel: 'Bash',
      correlationKey: corr,
    });
  }

  private _finalizeActiveOutput(isError: boolean): void {
    if (!this._streamOutputKey) return;
    const corr = this._streamCorr()!;
    const buf = this._outputBuffers.get(corr) || '';
    const tail = buf.slice(-320).trim();

    this._upsert(corr, {
      status: isError ? 'error' : 'ok',
      subtitle: tail || (isError ? 'Stream ended (error)' : 'Done'),
      detail: buf.slice(-DETAIL_KEEP) || undefined,
    });

    this._outputBuffers.delete(corr);
    this._streamOutputKey = null;
    this._streamLane = 'chat';
  }

  private _upsert(
    correlationKey: string,
    partial: Partial<WorkbenchEntry> & {
      title?: string;
      kind?: WorkbenchEntryKind;
      lane?: WorkbenchLane;
    },
  ): void {
    if (!correlationKey) {
      if (!partial.title || !partial.kind || !partial.lane) return;
      this._push(partial as Omit<WorkbenchEntry, 'id' | 'ts'>);
      return;
    }
    this.entries.update((list) => {
      const idx = list.findIndex((e) => e.correlationKey === correlationKey);
      if (idx >= 0) {
        const copy = [...list];
        const prev = copy[idx];
        const mergedPaths =
          partial.filePaths?.length
            ? partial.filePaths
            : partial.filePath
              ? [partial.filePath]
              : prev.filePaths;
        copy[idx] = {
          ...prev,
          ...partial,
          correlationKey,
          filePaths: mergedPaths?.length ? mergedPaths : prev.filePaths,
          filePath:
            partial.filePath ?? mergedPaths?.[0] ?? prev.filePath,
          ts: partial.status === 'running' ? prev.ts : Date.now(),
        };
        return copy;
      }
      const row: WorkbenchEntry = {
        id: newId(),
        ts: Date.now(),
        status: partial.status ?? 'running',
        title: partial.title ?? 'Event',
        kind: partial.kind ?? 'tool',
        lane: partial.lane ?? 'chat',
        ...partial,
        correlationKey,
      };
      return [...list, row].slice(-MAX_ENTRIES);
    });
  }

  recordFromRuntime(msg: any): void {
    if (!msg) return;
    const t = msg.type;
    const isSubAgent = msg.sub_agent === true;
    const dlgNum = delegateNumberFromMessage(msg);
    const lane: WorkbenchLane = msg.autonomous === true ? 'background' : 'chat';
    const corrNs = isSubAgent ? `sa${dlgNum || 0}-` : '';

    switch (t) {
      case 'agentic_start':
        if (isSubAgent) break;
        this._upsert(`${corrNs}agentic`, {
          lane,
          kind: 'agentic',
          title: msg.autonomous ? 'Background task' : 'Agent task started',
          subtitle: msg.autonomous
            ? (msg.task_preview || 'Working…').slice(0, 120)
            : `Up to ${msg.max_steps || 15} steps`,
          status: 'running',
          toolLabel: 'Task',
        });
        break;

      case 'agentic_iteration': {
        if (msg.autonomous) {
          const bgStep = msg.step || 0;
          const bgMax = msg.max_steps || 15;
          const bgTools = (msg.tool_calls || []).map((tc: any) => tc.name).join(', ');
          this._upsert(`${corrNs}agentic`, {
            lane: 'background',
            kind: 'agentic',
            title: 'Background task',
            subtitle: bgTools
              ? `Step ${bgStep}/${bgMax}: ${bgTools}`
              : `Step ${bgStep}/${bgMax}`,
            status: 'running',
            toolLabel: 'Task',
          });
          break;
        }
        const step = msg.step || 0;
        const maxSteps = msg.max_steps || 15;
        const toolCalls = msg.tool_calls || [];
        const groupKey = `${corrNs}step-${step}`;
        if (toolCalls.length > 0) {
          this._tagToolsForIteration(groupKey, toolCalls, dlgNum);
        } else {
          this._upsert(groupKey, {
            lane: 'chat',
            kind: 'agentic',
            title: `Step ${step}/${maxSteps}`,
            subtitle: 'Thinking…',
            status: 'ok',
            toolLabel: 'Step',
            delegateNumber: dlgNum,
          });
        }
        for (const tc of toolCalls) {
          const cid = tc.call_id || '';
          if (cid) this._toolMetaByCallId.delete(cid);
        }
        break;
      }

      case 'communicate': {
        const text = (msg.message || '').trim();
        if (!text) break;
        const formatted = parseAgentMessageText(text);
        const commLane: WorkbenchLane = msg.autonomous ? 'background' : 'chat';
        const teamId = formatted.chips.find((c) => c.label === 'Team')?.value;
        const batchId = formatted.chips.find((c) => c.label === 'Batch')?.value;
        const corrKey = teamId
          ? `comms-${teamId}`
          : batchId
            ? `comms-batch-${batchId}`
            : `comms-${text.slice(0, 48)}`;
        this._upsert(corrKey, {
          lane: commLane,
          kind: 'activity',
          title: formatted.headline,
          subtitle: formatted.body?.slice(0, 200),
          chips: formatted.chips.length ? formatted.chips : undefined,
          detail: text.length > 200 ? text.slice(0, DETAIL_KEEP) : undefined,
          status: 'ok',
          toolLabel: 'Comms',
        });
        break;
      }

      case 'agentic_complete': {
        if (isSubAgent) break;
        if (msg.autonomous) {
          const bgAborted = msg.aborted || false;
          const silent = isSilentAutonomousCompletion(msg);
          if (silent) break;
          const bgSteps = msg.total_steps || 0;
          const bgDur = ((msg.duration_ms || 0) / 1000).toFixed(0);
          this._upsert('bg-agentic-done', {
            lane: 'background',
            kind: 'agentic',
            title: bgAborted ? 'Background task stopped' : 'Background task completed',
            subtitle: `${bgSteps} steps, ${bgDur}s`,
            status: bgAborted ? 'error' : 'ok',
            toolLabel: 'Task',
          });
          break;
        }
        const exitReason = String(
          msg.exit_reason || msg.abort_reason || '',
        ).trim();
        const silentYield = isSilentOrchestrationExit(exitReason);
        const aborted = (msg.aborted || false) && !silentYield;
        const totalSteps = msg.total_steps || 0;
        const totalToolCalls = msg.total_tool_calls || 0;
        const durationMs = msg.duration_ms || 0;
        let abortTitle = 'Task completed';
        if (silentYield) {
          abortTitle = orchestratorYieldLabel(exitReason) || 'Coordinator check-in complete';
        } else if (aborted) {
          abortTitle = msg.autonomous
            ? 'Check-in cancelled'
            : (exitReason === 'user_abort' ? 'Stopped by user' : 'Task stopped');
        }
        this._upsert(`${corrNs}agentic-done`, {
          lane: 'chat',
          kind: 'agentic',
          title: abortTitle,
          subtitle: `${totalSteps} steps, ${totalToolCalls} tools, ${(durationMs / 1000).toFixed(1)}s`,
          status: aborted ? 'error' : 'ok',
          toolLabel: 'Task',
        });
        break;
      }

      case 'tool_execution_start': {
        const toolName = msg.tool_name || '';
        const args = (msg.arguments || {}) as Record<string, unknown>;
        const parsed = toolWorkbenchTitle(toolName, args, {
          lastMode: this._lastAgentMode,
        });
        if (toolName === 'switch_mode' && parsed.modeTransition) {
          this._lastAgentMode = parsed.modeTransition;
        }
        const callId = msg.call_id || '';
        const corr = `${corrNs}${callId || toolName}`;
        if (!isSubAgent) {
          this._finalizeActiveOutput(false);
        }
        if (toolName === 'bash' && !isSubAgent) {
          this._streamOutputKey = callId || `bash-${Date.now()}`;
          this._streamLane = lane;
        } else if (!isSubAgent) {
          this._streamOutputKey = null;
          this._streamLane = 'chat';
        }
        const title = parsed.title;
        this._toolStartTitles.set(corr, title);
        if (callId) {
          this._toolMetaByCallId.set(callId, {
            title: parsed.title,
            subtitle: parsed.subtitle,
          });
        }
        let filePaths = collectFilePaths(toolName, args);
        filePaths = this._enrichPaths(filePaths);
        if (toolName === 'plan') {
          this.workspaceCtx.noteProjectDirFromText(
            this._agentId(),
            JSON.stringify(args),
          );
        }
        const modeArrow = toolName === 'switch_mode' ? parsed.title : '';
        const modeParts = modeArrow.match(/^(.+?)\s*→\s*(.+)$/);
        const startChips =
          toolName === 'switch_mode' && modeParts
            ? [
                { label: 'From', value: modeParts[1].trim(), tone: 'muted' as const },
                { label: 'To', value: modeParts[2].trim(), tone: 'accent' as const },
              ]
            : undefined;
        this._upsert(corr, {
          lane,
          kind: 'tool',
          title: toolName === 'switch_mode' ? 'Switch mode' : title,
          subtitle: parsed.subtitle,
          status: 'running',
          toolLabel: parsed.toolLabel,
          mode: parsed.modeTransition,
          chips: startChips,
          delegateNumber: dlgNum,
          ...(filePaths.length
            ? { filePaths, filePath: filePaths[0] }
            : {}),
        });
        break;
      }

      case 'tool_execution_end': {
        const toolName = msg.tool_name || '';
        const isError = msg.is_error || false;
        const rawPreview = (msg.result_preview || '').slice(0, 400);
        const preview = cleanToolResultPreview(toolName, rawPreview);
        const callId = msg.call_id || '';
        const corr = `${corrNs}${callId || toolName}`;
        this._maybeTagPendingToolEnd(callId, dlgNum);
        if (toolName === 'bash' && !isSubAgent) {
          this._finalizeActiveOutput(isError);
          break;
        }
        const startTitle =
          this._stripSubAgentTitlePrefix(this._toolStartTitles.get(corr) || '')
          || toolName
          || 'Tool';
        this._toolStartTitles.delete(corr);
        const endTitle = this._stripSubAgentTitlePrefix(
          toolWorkbenchEndTitle(toolName, isError, startTitle),
        );
        const endArgs = normalizeToolArguments(msg.arguments || {});
        let filePaths = collectFilePaths(
          toolName,
          endArgs,
          rawPreview,
        );
        filePaths = this._enrichPaths(filePaths);
        if (toolName === 'plan') {
          this.workspaceCtx.noteProjectDirFromText(
            this._agentId(),
            rawPreview,
          );
        }
        let chips: ActivityChip[] | undefined;
        if (toolName === 'switch_mode') {
          const toMode = String(endArgs['mode'] || this._lastAgentMode || '');
          const metaTitle = callId
            ? this._toolMetaByCallId.get(callId)?.title
            : undefined;
          const arrow = (metaTitle || startTitle).match(/^(.+?)\s*→\s*(.+)$/);
          chips = [
            { label: 'From', value: arrow?.[1]?.trim() || '?', tone: 'muted' },
            {
              label: 'To',
              value: arrow?.[2]?.trim() || formatAgentMode(toMode),
              tone: 'accent',
            },
          ];
        } else if (toolName === 'team') {
          const pres = teamWorkbenchPresentation(endArgs, preview, {
            delegateNumber: dlgNum,
          });
          chips = pres.chips;
          if (isError && preview) {
            chips = [
              ...(chips ?? []),
              {
                label: 'Error',
                value: preview.slice(0, 2400),
                tone: 'warn' as const,
                variant: 'block' as const,
              },
            ];
          }
          this._upsert(corr, {
            title: pres.title,
            subtitle: isError
              ? preview.slice(0, 280) || pres.subtitle
              : pres.subtitle,
            chips,
            delegateNumber: dlgNum,
            ...(filePaths.length
              ? { filePaths, filePath: filePaths[0] }
              : {}),
            status: isError ? 'error' : 'ok',
            ...(isError && rawPreview
              ? { detail: rawPreview.slice(0, DETAIL_KEEP) }
              : {}),
          });
          break;
        } else if (toolName === 'plan') {
          const pres = planWorkbenchPresentation(endArgs, preview);
          this._upsert(corr, {
            title: pres.title,
            subtitle: pres.subtitle,
            chips: pres.chips,
            delegateNumber: dlgNum,
            ...(filePaths.length
              ? { filePaths, filePath: filePaths[0] }
              : {}),
            status: isError ? 'error' : 'ok',
          });
          break;
        } else if (preview) {
          chips = previewToChips(
            toolName,
            stripPathFromPreview(preview, filePaths[0]),
          );
        }
        const subtitleBody = chips?.length
          ? undefined
          : stripPathFromPreview(preview, filePaths[0]) || undefined;
        if (isError && preview) {
          chips = [
            ...(chips ?? []),
            {
              label: 'Error',
              value: preview.slice(0, 2400),
              tone: 'warn' as const,
              variant: 'block' as const,
            },
          ];
        }
        this._upsert(corr, {
          title:
            toolName === 'switch_mode'
              ? 'Switch mode'
              : this._stripSubAgentTitlePrefix(endTitle),
          subtitle:
            toolName === 'switch_mode'
              ? String(endArgs['reason'] || '').trim().slice(0, 120) || undefined
              : isError
                ? preview.slice(0, 280) || subtitleBody
                : subtitleBody,
          chips,
          delegateNumber: dlgNum,
          ...(filePaths.length
            ? { filePaths, filePath: filePaths[0] }
            : {}),
          status: isError ? 'error' : 'ok',
          ...(isError && rawPreview
            ? { detail: rawPreview.slice(0, DETAIL_KEEP) }
            : {}),
        });
        break;
      }

      case 'tool_output_chunk': {
        const chunk = msg.chunk || '';
        if (!chunk || isSubAgent) break;
        const toolName = (msg.tool_name || 'bash').toString();
        if (!this._streamOutputKey) {
          this._streamOutputKey = `orphan-${toolName}-${Date.now()}`;
          this._streamLane = msg.autonomous === true ? 'background' : 'chat';
        }
        this._mergeStreamOutput(toolName, chunk);
        break;
      }

      case 'delegate_start': {
        const dNum =
          typeof msg.delegate_number === 'number' && msg.delegate_number >= 0
            ? msg.delegate_number
            : 0;
        const dTask = (msg.delegate_task || 'Sub-task').slice(0, 120);
        this._upsert(`delegate-${dNum}`, {
          lane,
          kind: 'agentic',
          title: `Sub-agent #${dNum} spawned`,
          subtitle: dTask,
          status: 'running',
          toolLabel: 'Delegate',
          delegateNumber: dNum,
        });
        break;
      }

      case 'delegate_end': {
        const dNum =
          typeof msg.delegate_number === 'number' && msg.delegate_number >= 0
            ? msg.delegate_number
            : 0;
        const aborted = msg.aborted || false;
        const summary = (msg.summary || '').slice(0, 200);
        const iters = msg.iterations || 0;
        const tc = msg.tool_calls || 0;
        this._upsert(`delegate-${dNum}`, {
          lane,
          kind: 'agentic',
          title: aborted
            ? `Sub-agent #${dNum} stopped`
            : `Sub-agent #${dNum} completed`,
          subtitle: summary || `${iters} steps, ${tc} tools`,
          status: aborted ? 'error' : 'ok',
          toolLabel: 'Delegate',
          delegateNumber: dNum,
        });
        break;
      }

      case 'delegate_progress': {
        if (this.density() === 'focused') {
          break;
        }
        const dNum = msg.delegate_number || 0;
        const iter = msg.iteration || 0;
        const maxIter = msg.max_iterations || 0;
        const elapsed = Math.round(msg.elapsed_seconds || 0);
        this._upsert(`delegate-progress-${dNum}`, {
          lane,
          kind: 'activity',
          title: `Sub #${dNum}: ${iter}/${maxIter} (${elapsed}s)`,
          subtitle: (msg.task || '').slice(0, 80) || undefined,
          status: 'running',
          toolLabel: 'Delegate',
          delegateNumber: dNum,
        });
        break;
      }

      case 'delegate_batch_complete': {
        const count = msg.count || 0;
        this._push({
          lane,
          kind: 'agentic',
          title: `All ${count} sub-agents completed`,
          subtitle: 'Compiling results…',
          status: 'ok',
          toolLabel: 'Team',
          correlationKey: `batch-${msg.batch_id || 'done'}`,
        });
        break;
      }

      case 'activity_status': {
        const text = (msg.text || msg.message || msg.content || '').trim();
        if (!text) break;
        const formatted = parseAgentMessageText(text);
        const teamId = formatted.chips.find((c) => c.label === 'Team')?.value;
        const isCrunching = /crunching\s+data/i.test(text);
        const isOrchestratorPing =
          formatted.chips.some((c) => c.label === 'Check-in') || !!teamId;

        if (isCrunching) {
          this._upsert(`${corrNs}agentic`, {
            lane,
            kind: 'agentic',
            title: formatted.headline || 'Background task',
            subtitle: text.slice(0, 160),
            chips: formatted.chips.length ? formatted.chips : undefined,
            status: 'running',
            toolLabel: 'Task',
          });
          break;
        }

        if (isOrchestratorPing && teamId) {
          this._upsert(`comms-${teamId}`, {
            lane,
            kind: 'activity',
            title: formatted.headline || text.slice(0, 120),
            subtitle: formatted.body?.slice(0, 160) || text.slice(0, 160),
            chips: formatted.chips.length ? formatted.chips : undefined,
            status: 'running',
            toolLabel: 'Comms',
          });
          break;
        }

        this._upsert(`${corrNs}activity-status`, {
          lane,
          kind: 'activity',
          title: formatted.headline || text.slice(0, 120),
          subtitle:
            formatted.body?.slice(0, 160)
            || (msg.autonomous ? 'Background' : undefined),
          chips: formatted.chips.length ? formatted.chips : undefined,
          status: 'running',
          toolLabel: 'Activity',
        });
        break;
      }

      default:
        break;
    }
  }

  private _push(partial: Omit<WorkbenchEntry, 'id' | 'ts'>): void {
    const row: WorkbenchEntry = {
      id: newId(),
      ts: Date.now(),
      ...partial,
      lane: partial.lane ?? 'chat',
    };
    this.entries.update((list) => [...list, row].slice(-MAX_ENTRIES));
  }
}
