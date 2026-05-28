import { Component, Input, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterModule } from '@angular/router';
import { RunViewService } from '../../../core/services/run-view.service';
import type { RunDelegate, RunDelegateToolCall, RunStep } from '../../../core/models/run-view.model';

@Component({
  selector: 'app-run-panel',
  standalone: true,
  imports: [CommonModule, RouterModule],
  templateUrl: './run-panel.component.html',
  styleUrl: './run-panel.component.scss',
})
export class RunPanelComponent {
  readonly run = inject(RunViewService);

  @Input() mode: 'chat' | 'projects' = 'chat';
  @Input() agentId = '';
  @Input() agenticActive = false;

  toolLimit(): number {
    return this.run.toolPreviewLimit(this.mode);
  }

  visibleTools(delegate: RunDelegate): RunDelegateToolCall[] {
    const limit = this.toolLimit();
    const calls = delegate.toolCalls || [];
    if (calls.length <= limit) return calls;
    return calls.slice(-limit);
  }

  hiddenToolCount(delegate: RunDelegate): number {
    const n = (delegate.toolCalls || []).length - this.toolLimit();
    return n > 0 ? n : 0;
  }

  stepDelegates(step: RunStep): RunDelegate[] {
    return step.delegates || [];
  }

  stepHasActivity(step: RunStep): boolean {
    return (
      step.status === 'active'
      || step.delegates.some(d => d.status === 'running' || d.status === 'queued')
    );
  }

  toggleStepExpand(stepId: string, expanded: Set<string>): void {
    if (expanded.has(stepId)) expanded.delete(stepId);
    else expanded.add(stepId);
  }

  expandedSteps = new Set<string>();

  isStepExpanded(step: RunStep): boolean {
    if (this.expandedSteps.has(step.id)) return true;
    if (!this.run.expanded()) return false;
    return step.delegates.length > 0;
  }

  onStepHeaderClick(step: RunStep): void {
    if (this.expandedSteps.has(step.id)) this.expandedSteps.delete(step.id);
    else this.expandedSteps.add(step.id);
  }

  delegateTitle(d: RunDelegate): string {
    return taskTitle(d.task);
  }

  delegateLabel(d: RunDelegate): string {
    if (d.number >= 0) return `#${d.number}`;
    if (d.memberIdx != null) return `slot ${d.memberIdx + 1}`;
    return '#?';
  }

  delegateTrackKey(d: RunDelegate): string {
    return d.memberKey ?? (d.number >= 0 ? `n:${d.number}` : `s:${d.stepId}`);
  }

  toolPath(tc: RunDelegateToolCall): string {
    const a = tc.args || {};
    return String(a['path'] || a['file_path'] || '');
  }

  toolCommand(tc: RunDelegateToolCall): string {
    const a = tc.args || {};
    return String(a['command'] || '');
  }

  projectsLink(): string[] {
    return this.agentId ? ['/projects', this.agentId] : [];
  }
}

function taskTitle(task: string): string {
  const line = (task || '').split('\n').find(l => l.trim()) || task;
  const stripped = line.replace(/^#\d+\s+/, '').trim() || line;
  return stripped.length > 100 ? stripped.slice(0, 97) + '…' : stripped;
}
