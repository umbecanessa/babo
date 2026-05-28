import { Component, input } from '@angular/core';
import { CommonModule } from '@angular/common';

export interface AgentTool {
  name: string;
  description?: string;
}

@Component({
  selector: 'app-tool-card',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="tool-card">
      <span class="tool-name">{{ tool().name }}</span>
      @if (tool().description) {
        <span class="tool-desc">{{ tool().description }}</span>
      }
      <span class="tool-badge">Active</span>
    </div>
  `,
  styles: [`
    :host { display: block; }
    .tool-card {
      display: flex;
      flex-direction: column;
      gap: 6px;
      padding: 16px 18px;
      background: var(--overlay-1);
      border: 1px solid var(--overlay-3);
      border-radius: 10px;
      min-height: 100px;
    }
    .tool-name {
      font-size: 14px;
      font-weight: 600;
      color: var(--text-primary);
      font-family: 'JetBrains Mono', 'Fira Code', 'Consolas', monospace;
    }
    .tool-desc {
      font-size: 12px;
      color: var(--text-muted);
      line-height: 1.4;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }
    .tool-badge {
      display: inline-block;
      width: fit-content;
      margin-top: auto;
      padding: 3px 10px;
      font-size: 10px;
      font-weight: 600;
      color: var(--accent-success);
      background: rgba(52, 211, 153, 0.12);
      border: 1px solid rgba(52, 211, 153, 0.2);
      border-radius: 20px;
    }
  `],
})
export class ToolCardComponent {
  tool = input.required<AgentTool>();
}
