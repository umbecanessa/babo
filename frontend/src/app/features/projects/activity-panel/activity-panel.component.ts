import { Component, Input } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Team, TodoItem, PlanSummary } from '../project.models';

interface ActivityEntry {
  id: string;
  time: number;
  icon: string;
  text: string;
  category: 'team' | 'member' | 'todo' | 'plan';
  detail?: string;
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
  imports: [CommonModule],
  templateUrl: './activity-panel.component.html',
  styleUrl: './activity-panel.component.scss',
})
export class ActivityPanelComponent {
  @Input() teams: Team[] = [];
  @Input() items: TodoItem[] = [];
  @Input() plansByTodoId: Record<string, PlanSummary> = {};

  expandedEntries = new Set<string>();

  get entries(): ActivityEntry[] {
    const entries: ActivityEntry[] = [];

    for (const team of this.teams) {
      if (team.status === 'active' && team.created_at) {
        entries.push({
          id: `t-${team.id}-launched`,
          time: team.created_at,
          icon: '\u25B6',
          text: `Team "${team.name}" launched (${team.members.length} members)`,
          category: 'team',
          detail: team.briefing || (team.mission ? `Mission: ${team.mission}` : undefined),
          teamName: team.name,
        });
      } else {
        entries.push({
          id: `t-${team.id}-created`,
          time: team.created_at,
          icon: '\u2699',
          text: `Team "${team.name}" created (${team.members.length} members)`,
          category: 'team',
          detail: team.mission ? `Mission: ${team.mission}` : undefined,
          teamName: team.name,
        });
      }

      if (team.status === 'paused') {
        entries.push({
          id: `t-${team.id}-paused`,
          time: team.created_at + 2,
          icon: '\u23F8',
          text: `Team "${team.name}" paused`,
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
          text: `Team "${team.name}" completed`,
          category: 'team',
          detail: `${doneCount}/${team.members.length} members finished successfully.`,
          teamName: team.name,
        });
      }

      if (team.status === 'failed' && team.completed_at) {
        entries.push({
          id: `t-${team.id}-failed`,
          time: team.completed_at,
          icon: '\u2717',
          text: `Team "${team.name}" disbanded`,
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
            text: `#${member.delegate_number} working: ${member.task}`,
            category: 'member',
            detail: `Iteration ${member.iterations}, ${member.tool_calls} tool calls so far.`,
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
            text: `#${member.delegate_number} completed: ${member.task}`,
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
            text: `#${member.delegate_number} failed: ${member.task}`,
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
          text: `Todo completed: ${item.title}`,
          category: 'todo',
          detail: item.description || undefined,
        });
      } else if (item.status === 'in_progress') {
        entries.push({
          id: `todo-${item.id}-prog`,
          time: item.updated_at,
          icon: '\u25B6',
          text: `Todo started: ${item.title}`,
          category: 'todo',
          detail: item.description || undefined,
        });
      } else if (item.created_at === item.updated_at && item.status !== 'done') {
        entries.push({
          id: `todo-${item.id}-add`,
          time: item.created_at,
          icon: '\u002B',
          text: `Todo added: ${item.title}`,
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
          text: `Plan "${plan.title}": ${doneSteps.length}/${plan.steps.length} steps done`,
          category: 'plan',
          detail: `Recently completed: ${recent}`,
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
      case 'member': return '#a78bfa';
      case 'todo': return '#34d399';
      case 'plan': return '#fbbf24';
      default: return '#6b7280';
    }
  }

  formatTime(ts: number): string {
    if (!ts) return '';
    const d = new Date(ts * 1000);
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    if (diffMins < 1) return 'just now';
    if (diffMins < 60) return `${diffMins}m ago`;
    const diffHrs = Math.floor(diffMins / 60);
    if (diffHrs < 24) return `${diffHrs}h ago`;
    return `${Math.floor(diffHrs / 24)}d ago`;
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
