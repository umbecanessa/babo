import { Injectable, signal, computed } from '@angular/core';

export type WorkbenchEntryKind = 'agentic' | 'tool' | 'activity';

/** Chat thread task vs autonomous / memory-background work */
export type WorkbenchLane = 'chat' | 'background';

export interface WorkbenchEntry {
  id: string;
  ts: number;
  kind: WorkbenchEntryKind;
  /** Foreground chat agentic run vs background autonomous task */
  lane: WorkbenchLane;
  title: string;
  subtitle?: string;
  status?: 'running' | 'ok' | 'error';
  /** tool call_id, agentic step, stream session id, etc. */
  correlationKey?: string;
  /** Longer text (e.g. accumulated bash output) — shown in expandable block */
  detail?: string;
  /** Sub-agent delegation number (0 = orchestrator) */
  delegateNumber?: number;
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
  private readonly _agentId = signal<string>('');

  readonly panelOpen = signal(false);
  readonly entries = signal<WorkbenchEntry[]>([]);

  readonly entryCount = computed(() => this.entries().length);

  /** Active bash (or other streamed) output session id — aligns with `out-${id}` rows */
  private _streamOutputKey: string | null = null;
  /** Lane for the active bash stream (chunks are not tagged on the wire). */
  private _streamLane: WorkbenchLane = 'chat';
  private readonly _outputBuffers = new Map<string, string>();

  bindAgent(agentId: string): void {
    if (this._agentId() === agentId) return;
    this._agentId.set(agentId);
    this.entries.set([]);
    this._resetStreamScratch();
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

  clear(): void {
    this.entries.set([]);
    this._resetStreamScratch();
  }

  /** Restore from snapshot (plain JSON). */
  restoreState(open: boolean, list: WorkbenchEntry[]): void {
    this._resetStreamScratch();
    this.panelOpen.set(open);
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

  snapshotState(): { open: boolean; entries: WorkbenchEntry[] } {
    return {
      open: this.panelOpen(),
      entries: this.entries().map((e) => ({ ...e })),
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

  private _streamCorr(): string | null {
    return this._streamOutputKey ? `out-${this._streamOutputKey}` : null;
  }

  /** Merge streamed tool stdout (bash) into one running row; finalize on tool_execution_end. */
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

    this.entries.update((list) => {
      const idx = list.findIndex((e) => e.correlationKey === corr);
      if (idx >= 0) {
        const copy = [...list];
        const prev = copy[idx];
        copy[idx] = {
          ...prev,
          subtitle: tail || '(streaming…)',
          detail,
          status: 'running',
          lane: prev.lane ?? this._streamLane,
        };
        return copy;
      }
      const row: WorkbenchEntry = {
        id: newId(),
        ts: Date.now(),
        lane: this._streamLane,
        kind: 'tool',
        title: `Live output (${toolName})`,
        subtitle: tail || '(streaming…)',
        detail,
        status: 'running',
        correlationKey: corr,
      };
      return [...list, row].slice(-MAX_ENTRIES);
    });
  }

  /** Close the current streamed output row (bash stdout), if any. */
  private _finalizeActiveOutput(isError: boolean): void {
    if (!this._streamOutputKey) return;
    const corr = this._streamCorr()!;
    const buf = this._outputBuffers.get(corr) || '';
    const tail = buf.slice(-320).trim();

    this.entries.update((list) =>
      list.map((e) => {
        if (e.correlationKey !== corr) return e;
        return {
          ...e,
          status: isError ? 'error' : 'ok',
          subtitle: tail || e.subtitle || (isError ? 'Stream ended (error)' : 'Done'),
          detail: buf.slice(-DETAIL_KEEP) || e.detail,
        };
      }),
    );
    this._outputBuffers.delete(corr);
    this._streamOutputKey = null;
    this._streamLane = 'chat';
  }

  /**
   * Derive workbench lines from runtime payloads (same stream as chat).
   */
  recordFromRuntime(msg: any): void {
    if (!msg) return;
    const t = msg.type;
    const isSubAgent = msg.sub_agent === true;
    const dlgNum: number | undefined = isSubAgent
      ? (msg.delegate_number > 0 ? msg.delegate_number : undefined)
      : undefined;
    const lane: WorkbenchLane = msg.autonomous === true ? 'background' : 'chat';
    const dlgPrefix = isSubAgent
      ? (dlgNum ? `[Sub #${dlgNum}] ` : '[Sub] ')
      : '';
    const corrNs = isSubAgent ? `sa${dlgNum || 0}-` : '';
    switch (t) {
      case 'agentic_start':
        if (isSubAgent) break;
        this._push({
          lane,
          kind: 'agentic',
          title: msg.autonomous ? 'Background task' : 'Agent task started',
          subtitle: msg.autonomous
            ? (msg.task_preview || 'Working…').slice(0, 120)
            : `Up to ${msg.max_steps || 15} steps`,
          status: 'running',
          correlationKey: 'agentic',
        });
        break;
      case 'agentic_iteration': {
        if (msg.autonomous) {
          const bgStep = msg.step || 0;
          const bgMax = msg.max_steps || 15;
          const bgTools = (msg.tool_calls || []).map((tc: any) => tc.name).join(', ');
          this._push({
            lane: 'background',
            kind: 'agentic',
            title: `Background step ${bgStep}/${bgMax}`,
            subtitle: bgTools || 'processing',
            status: 'running',
            correlationKey: `bg-${bgStep}`,
          });
          break;
        }
        const step = msg.step || 0;
        const maxSteps = msg.max_steps || 15;
        const toolCalls = msg.tool_calls || [];
        const toolResults = msg.tool_results || [];
        const toolNames = toolCalls.map((tc: any) => tc.name).join(', ');
        const successes = toolResults.filter((r: any) => r.success).length;
        this._push({
          lane: 'chat',
          kind: 'agentic',
          title: `${dlgPrefix}Step ${step}/${maxSteps}`,
          subtitle: toolNames
            ? `${toolNames} (${successes}/${toolResults.length} ok)`
            : undefined,
          status: 'ok',
          correlationKey: `${corrNs}step-${step}`,
          delegateNumber: dlgNum,
        });
        break;
      }
      case 'agentic_complete': {
        if (isSubAgent) break;
        if (msg.autonomous) {
          const bgAborted = msg.aborted || false;
          const bgSteps = msg.total_steps || 0;
          const bgDur = ((msg.duration_ms || 0) / 1000).toFixed(0);
          this._push({
            lane: 'background',
            kind: 'agentic',
            title: bgAborted ? 'Background task stopped' : 'Background task completed',
            subtitle: `${bgSteps} steps, ${bgDur}s`,
            status: bgAborted ? 'error' : 'ok',
            correlationKey: 'agentic-done',
          });
          break;
        }
        const aborted = msg.aborted || false;
        const totalSteps = msg.total_steps || 0;
        const totalToolCalls = msg.total_tool_calls || 0;
        const durationMs = msg.duration_ms || 0;
        this._push({
          lane: 'chat',
          kind: 'agentic',
          title: aborted ? 'Task stopped' : 'Task completed',
          subtitle: `${totalSteps} steps, ${totalToolCalls} tools, ${(durationMs / 1000).toFixed(1)}s`,
          status: aborted ? 'error' : 'ok',
          correlationKey: 'agentic-done',
        });
        break;
      }
      case 'tool_execution_start': {
        const toolName = msg.tool_name || '';
        const args = msg.arguments || {};
        const label = this._toolStartLabel(toolName, args);
        const callId = msg.call_id || '';
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
        this._push({
          lane,
          kind: 'tool',
          title: `${dlgPrefix}${label}`,
          subtitle: toolName || undefined,
          status: 'running',
          correlationKey: `${corrNs}${callId || toolName}`,
          delegateNumber: dlgNum,
        });
        break;
      }
      case 'tool_execution_end': {
        const toolName = msg.tool_name || '';
        const isError = msg.is_error || false;
        const preview = (msg.result_preview || '').slice(0, 200);
        const callId = msg.call_id || '';
        if (toolName === 'bash' && !isSubAgent) {
          this._finalizeActiveOutput(isError);
        }
        this._push({
          lane,
          kind: 'tool',
          title: isError
            ? `${dlgPrefix}${toolName || 'Tool'} failed`
            : `${dlgPrefix}${toolName || 'Tool'} finished`,
          subtitle: preview || undefined,
          status: isError ? 'error' : 'ok',
          correlationKey: `${corrNs}${callId || toolName}`,
          delegateNumber: dlgNum,
        });
        break;
      }
      case 'tool_output_chunk': {
        const chunk = msg.chunk || '';
        if (!chunk) break;
        if (isSubAgent) break;
        const toolName = (msg.tool_name || 'bash').toString();
        if (!this._streamOutputKey) {
          this._streamOutputKey = `orphan-${toolName}-${Date.now()}`;
          if (msg.autonomous === true) {
            this._streamLane = 'background';
          } else if (msg.autonomous === false) {
            this._streamLane = 'chat';
          }
        }
        this._mergeStreamOutput(toolName, chunk);
        break;
      }
      case 'delegate_start': {
        const dNum = msg.delegate_number || 1;
        const dTask = (msg.delegate_task || 'Sub-task').slice(0, 120);
        this._push({
          lane,
          kind: 'agentic',
          title: `Sub-agent #${dNum} spawned`,
          subtitle: dTask,
          status: 'running',
          correlationKey: `delegate-${dNum}`,
          delegateNumber: dNum,
        });
        break;
      }
      case 'delegate_end': {
        const dNum = msg.delegate_number || 1;
        const aborted = msg.aborted || false;
        const summary = (msg.summary || '').slice(0, 200);
        const iters = msg.iterations || 0;
        const tc = msg.tool_calls || 0;
        this._push({
          lane,
          kind: 'agentic',
          title: aborted
            ? `Sub-agent #${dNum} stopped`
            : `Sub-agent #${dNum} completed`,
          subtitle: summary || `${iters} steps, ${tc} tools`,
          status: aborted ? 'error' : 'ok',
          correlationKey: `delegate-${dNum}`,
          delegateNumber: dNum,
        });
        break;
      }
      case 'delegate_progress': {
        const dNum = msg.delegate_number || 0;
        const iter = msg.iteration || 0;
        const maxIter = msg.max_iterations || 0;
        const elapsed = Math.round(msg.elapsed_seconds || 0);
        this._push({
          lane,
          kind: 'activity',
          title: `Sub-agent #${dNum}: ${iter}/${maxIter} iters (${elapsed}s)`,
          subtitle: (msg.task || '').slice(0, 80) || undefined,
          status: 'running',
          correlationKey: `delegate-progress-${dNum}`,
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
          correlationKey: `batch-${msg.batch_id || 'done'}`,
        });
        break;
      }
      case 'activity_status': {
        const text = msg.text || msg.message || msg.content || '';
        if (!text) break;
        this._push({
          lane,
          kind: 'activity',
          title: text.slice(0, 200),
          subtitle: msg.autonomous ? 'Background' : undefined,
          status: 'running',
        });
        break;
      }
      default:
        break;
    }
  }

  private _toolStartLabel(toolName: string, args: Record<string, unknown>): string {
    if (toolName === 'write' || toolName === 'write_file' || toolName === 'create_file') {
      return `Writing ${(args['path'] || args['file_path'] || 'file') as string}`;
    }
    if (toolName === 'bash') {
      const cmd = args['command'] as string | undefined;
      return cmd ? `$ ${cmd.slice(0, 80)}` : 'Running command…';
    }
    if (toolName === 'read' || toolName === 'read_file') {
      return `Reading ${(args['path'] || args['file_path'] || 'file') as string}`;
    }
    if (toolName === 'edit') {
      return `Editing ${(args['path'] || args['file_path'] || 'file') as string}`;
    }
    if (toolName === 'web_search' || toolName === 'search') {
      return `Search: ${(args['query'] || args['term'] || '') as string}`.slice(0, 120);
    }
    if (toolName === 'web_fetch') {
      return `Fetch: ${String(args['url'] || '').slice(0, 80)}`;
    }
    if (toolName === 'browser_navigate') {
      return `Browse: ${String(args['url'] || '').slice(0, 80)}`;
    }
    if (toolName === 'delegate') {
      return `Delegate: ${String(args['task'] || '').slice(0, 80)}`;
    }
    return toolName ? `Running ${toolName}…` : 'Running tool…';
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
