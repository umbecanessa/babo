import { Component, Input, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { TranslateModule, TranslateService } from '@ngx-translate/core';
import { Team, TodoItem, PlanSummary } from '../project.models';
import type { RunDelegate } from '../../../core/models/run-view.model';

interface ActivityEntry {
  id: string;
  time: number;
  icon: string;
  textKey: string;
  textParams?: Record<string, string | number>;
  category: 'team' | 'member' | 'todo' | 'plan' | 'delegate';
  detail?: string;
  detailKey?: string;
  detailParams?: Record<string, string | number>;
  teamName?: string;
  memberTask?: string;
  resultSummary?: string;
  iterations?: number;
  toolCalls?: number;
  elapsed?: number;
}

@Component({
  selector: 'app-activity-panel',
  standalone: true,
  imports: [CommonModule, TranslateModule],
  templateUrl: './activity-panel.component.html',
  styleUrl: './activity-panel.component.scss',
})
export class ActivityPanelComponent {
  @Input() teams: Team[] = [];
  @Input() items: TodoItem[] = [];
  @Input() plansByTodoId: Record<string, PlanSummary> = {};
  /** Live sub-agents from RunView (simple delegate batches). */
  @Input() liveDelegates: RunDelegate[] = [];
  /** Compact layout for Overview side column */
  @Input() compact = false;
  @Input() maxEntries = 0;

  expandedEntries = new Set<string>();

  private readonly translate = inject(TranslateService);

  get entries(): ActivityEntry[] {
    let entries = this.buildEntries();
    if (this.maxEntries > 0 && entries.length > this.maxEntries) {
      entries = entries.slice(0, this.maxEntries);
    }
    return entries;
  }

  private buildEntries(): ActivityEntry[] {
    const entries: ActivityEntry[] = [];
    const now = Date.now() / 1000;

    for (const d of this.liveDelegates) {
      entries.push({
        id: `live-delegate-${d.number}`,
        time: now,
        icon: d.status === 'queued' ? '\u23F3' : '\u21BB',
        textKey: d.status === 'queued'
          ? 'projects.activity.events.delegateQueued'
          : 'projects.activity.events.delegateWorking',
        textParams: {
          number: d.number >= 0 ? d.number : '?',
          task: taskHeadline(d.task),
        },
        category: 'delegate',
        memberTask: d.task,
        detailKey: d.batchId ? 'projects.activity.events.batch' : undefined,
        detailParams: d.batchId ? { id: d.batchId } : undefined,
        iterations: d.iterations,
        toolCalls: d.totalToolCalls ?? d.toolCalls?.length,
      });
    }

    for (const team of this.teams) {
      if (team.status === 'active' && team.created_at) {
        entries.push({
          id: `t-${team.id}-launched`,
          time: team.created_at,
          icon: '\u25B6',
          textKey: 'projects.activity.events.teamLaunched',
          textParams: { name: team.name, count: team.members.length },
          category: 'team',
          detail: team.briefing || undefined,
          detailKey: !team.briefing && team.mission
            ? 'projects.activity.events.mission'
            : undefined,
          detailParams: !team.briefing && team.mission
            ? { mission: team.mission }
            : undefined,
          teamName: team.name,
        });
      } else {
        entries.push({
          id: `t-${team.id}-created`,
          time: team.created_at,
          icon: '\u2699',
          textKey: 'projects.activity.events.teamCreated',
          textParams: { name: team.name, count: team.members.length },
          category: 'team',
          detailKey: team.mission ? 'projects.activity.events.mission' : undefined,
          detailParams: team.mission ? { mission: team.mission } : undefined,
          teamName: team.name,
        });
      }

      if (team.status === 'paused') {
        entries.push({
          id: `t-${team.id}-paused`,
          time: team.created_at + 2,
          icon: '\u23F8',
          textKey: 'projects.activity.events.teamPaused',
          textParams: { name: team.name },
          category: 'team',
          teamName: team.name,
        });
      }

      if (team.status === 'completed' && team.completed_at) {
        const doneCount = team.members.filter(m => m.status === 'done').length;
        entries.push({
          id: `t-${team.id}-completed`,
          time: team.completed_at,
          icon: '\u2713',
          textKey: 'projects.activity.events.teamCompleted',
          textParams: { name: team.name },
          category: 'team',
          detailKey: 'projects.activity.events.teamCompletedDetail',
          detailParams: { done: doneCount, total: team.members.length },
          teamName: team.name,
        });
      }

      if (team.status === 'failed' && team.completed_at) {
        entries.push({
          id: `t-${team.id}-failed`,
          time: team.completed_at,
          icon: '\u2717',
          textKey: 'projects.activity.events.teamDisbanded',
          textParams: { name: team.name },
          category: 'team',
          teamName: team.name,
        });
      }

      for (const member of team.members) {
        if (member.status === 'running' && member.iterations > 0) {
          entries.push({
            id: `m-${team.id}-${member.delegate_number}-run`,
            time: team.created_at + 2,
            icon: '\u21BB',
            textKey: 'projects.activity.events.memberWorking',
            textParams: { number: member.delegate_number, task: member.task },
            category: 'member',
            detailKey: 'projects.activity.events.memberWorkingDetail',
            detailParams: {
              iterations: member.iterations,
              toolCalls: member.tool_calls,
            },
            teamName: team.name,
            memberTask: member.task,
            iterations: member.iterations,
            toolCalls: member.tool_calls,
          });
        }
        if (member.status === 'done' && member.elapsed_seconds > 0) {
          entries.push({
            id: `m-${team.id}-${member.delegate_number}-done`,
            time: team.created_at + member.elapsed_seconds,
            icon: '\u2713',
            textKey: 'projects.activity.events.memberCompleted',
            textParams: { number: member.delegate_number, task: member.task },
            category: 'member',
            detail: member.result_summary || undefined,
            teamName: team.name,
            memberTask: member.task,
            resultSummary: member.result_summary,
            iterations: member.iterations,
            toolCalls: member.tool_calls,
            elapsed: member.elapsed_seconds,
          });
        }
        if (member.status === 'failed') {
          entries.push({
            id: `m-${team.id}-${member.delegate_number}-fail`,
            time: team.created_at + (member.elapsed_seconds || 1),
            icon: '\u2717',
            textKey: 'projects.activity.events.memberFailed',
            textParams: { number: member.delegate_number, task: member.task },
            category: 'member',
            detail: member.result_summary || undefined,
            teamName: team.name,
            memberTask: member.task,
            resultSummary: member.result_summary,
            elapsed: member.elapsed_seconds,
          });
        }
      }
    }

    const recentItems = [...this.items]
      .filter(i => i.updated_at > 0)
      .sort((a, b) => b.updated_at - a.updated_at)
      .slice(0, 15);

    for (const item of recentItems) {
      if (item.status === 'done' && item.completed_at) {
        entries.push({
          id: `todo-${item.id}-done`,
          time: item.completed_at,
          icon: '\u2611',
          textKey: 'projects.activity.events.todoCompleted',
          textParams: { title: item.title },
          category: 'todo',
          detail: item.description || undefined,
        });
      } else if (item.status === 'in_progress') {
        entries.push({
          id: `todo-${item.id}-prog`,
          time: item.updated_at,
          icon: '\u25B6',
          textKey: 'projects.activity.events.todoStarted',
          textParams: { title: item.title },
          category: 'todo',
          detail: item.description || undefined,
        });
      } else if (item.created_at === item.updated_at && item.status !== 'done') {
        entries.push({
          id: `todo-${item.id}-add`,
          time: item.created_at,
          icon: '\u002B',
          textKey: 'projects.activity.events.todoAdded',
          textParams: { title: item.title },
          category: 'todo',
          detail: item.description || undefined,
        });
      }
    }

    for (const [todoId, plan] of Object.entries(this.plansByTodoId)) {
      if (!plan?.steps) continue;
      const doneSteps = plan.steps.filter(s => s.status === 'done');
      if (doneSteps.length > 0 && doneSteps.length < plan.steps.length) {
        const recent = doneSteps.map(s => s.label).slice(-3).join(', ');
        entries.push({
          id: `plan-${todoId}`,
          time: Date.now() / 1000 - 30,
          icon: '\u2699',
          textKey: 'projects.activity.events.planProgress',
          textParams: {
            title: plan.title,
            done: doneSteps.length,
            total: plan.steps.length,
          },
          category: 'plan',
          detailKey: 'projects.activity.events.planRecent',
          detailParams: { recent },
        });
      }
    }

    return entries.sort((a, b) => b.time - a.time).slice(0, 40);
  }

  toggleExpand(id: string): void {
    if (this.expandedEntries.has(id)) {
      this.expandedEntries.delete(id);
    } else {
      this.expandedEntries.add(id);
    }
  }

  isExpanded(id: string): boolean {
    return this.expandedEntries.has(id);
  }

  categoryColor(cat: string): string {
    switch (cat) {
      case 'team': return '#60a5fa';
      case 'member': return 'var(--accent-primary)';
      case 'todo': return 'var(--accent-success)';
      case 'plan': return 'var(--accent-warn)';
      case 'delegate': return 'var(--accent-primary)';
      default: return '#6b7280';
    }
  }

  formatTime(ts: number): string {
    if (!ts) return '';
    const d = new Date(ts * 1000);
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    if (diffMins < 1) return this.translate.instant('projects.activity.time.justNow');
    if (diffMins < 60) {
      return this.translate.instant('projects.activity.time.minutesAgo', { count: diffMins });
    }
    const diffHrs = Math.floor(diffMins / 60);
    if (diffHrs < 24) {
      return this.translate.instant('projects.activity.time.hoursAgo', { count: diffHrs });
    }
    return this.translate.instant('projects.activity.time.daysAgo', {
      count: Math.floor(diffHrs / 24),
    });
  }

  formatElapsed(seconds: number | undefined): string {
    if (!seconds || seconds <= 0) return '';
    if (seconds < 60) return `${Math.round(seconds)}s`;
    const mins = Math.floor(seconds / 60);
    if (mins < 60) return `${mins}m ${Math.round(seconds % 60)}s`;
    const hrs = Math.floor(mins / 60);
    return `${hrs}h ${mins % 60}m`;
  }
}

function taskHeadline(task: string): string {
  const line = (task || '').split('\n').find(l => l.trim()) || task;
  return line.length > 96 ? line.slice(0, 93) + '…' : line;
}
