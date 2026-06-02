import { Component, input, output } from '@angular/core';
import { CommonModule } from '@angular/common';

export interface SkillCardModel {
  name: string;
  version?: string;
  description?: string;
  status: 'loaded' | 'disabled' | 'error';
  enabled_for_agent?: boolean;
  created_by?: string;
  dependencies?: string[];
  error?: string;
  skill_type?: 'native' | 'agentskill' | 'hybrid';
  source?: 'bundled' | 'local' | 'clawhub';
  myelination_score?: number;
  crystallization_ready?: boolean;
  crystallized_from?: string | null;
}

@Component({
  selector: 'app-skill-card',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="skill-card"
         [class.status-loaded]="skill().status === 'loaded'"
         [class.status-disabled]="skill().status === 'disabled'"
         [class.status-error]="skill().status === 'error'"
         (click)="openDetail.emit()">
      <div class="skill-top">
        <span class="status-dot" [class]="'dot-' + skill().status"></span>
        <span class="skill-status-badge" [class]="'badge-' + skill().status">{{ skill().status }}</span>
        @if (skill().status === 'loaded') {
          <label class="skill-toggle" (click)="$event.stopPropagation()">
            <input type="checkbox"
              [checked]="skill().enabled_for_agent !== false"
              (change)="toggleEnabled.emit($any($event.target).checked)" />
          </label>
        }
      </div>
      <span class="skill-name">{{ skill().name }}</span>
      <span class="skill-version">v{{ skill().version || '?' }}</span>
      <div class="skill-badges">
        @if (skill().skill_type === 'agentskill') {
          <span class="type-badge agentskill">AgentSkill</span>
        } @else if (skill().skill_type === 'hybrid') {
          <span class="type-badge hybrid">Hybrid</span>
        } @else if (skill().skill_type === 'native') {
          <span class="type-badge native">NLS Plugin</span>
        }
        @if (skill().source === 'clawhub') {
          <span class="source-badge clawhub">ClawHub</span>
        } @else if (skill().source === 'bundled') {
          <span class="source-badge bundled">Bundled</span>
        }
        @if (skill().crystallization_ready) {
          <span class="crystal-badge">Ready to Upgrade</span>
        }
        @if (skill().crystallized_from) {
          <span class="lineage-badge">Evolved from: {{ skill().crystallized_from }}</span>
        }
      </div>
      @if (skill().myelination_score != null && skill().myelination_score! > 0) {
        <div class="myelination-bar">
          <div class="myelination-fill" [style.width.%]="(skill().myelination_score || 0) * 100"></div>
        </div>
      }
      @if (skill().description) {
        <span class="skill-desc">{{ skill().description }}</span>
      }
      @if (skill().error) {
        <span class="skill-error-hint">Has errors</span>
      }
    </div>
  `,
  styleUrls: ['./skill-card.component.scss'],
})
export class SkillCardComponent {
  skill = input.required<SkillCardModel>();
  openDetail = output<void>();
  toggleEnabled = output<boolean>();
}
