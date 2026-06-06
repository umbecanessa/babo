import {
  Component,
  HostListener,
  inject,
  signal,
  ElementRef,
  viewChild,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import {
  AgentOrchestrationProfileService,
  OrchestrationProfileChoice,
  ORCHESTRATION_PROFILE_OPTIONS,
} from '../../../core/services/agent-orchestration-profile.service';

@Component({
  selector: 'app-chat-orchestration-profile-picker',
  standalone: true,
  imports: [CommonModule],
  template: `
    <div class="profile-picker chip" (click)="$event.stopPropagation()">
      <button
        type="button"
        class="profile-trigger"
        (click)="toggleOpen()"
        [attr.aria-expanded]="open()"
        [title]="triggerTitle()"
      >
        <span class="profile-trigger-label">{{ triggerLabel() }}</span>
        @if (profiles.hasManualOverride()) {
          <span class="profile-override-dot" title="Manual profile override"></span>
        }
        <svg class="chevron" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polyline points="6 9 12 15 18 9"></polyline>
        </svg>
      </button>
      @if (open()) {
        <div class="profile-menu" role="listbox" (click)="$event.stopPropagation()">
          <div class="profile-menu-title">Orchestration depth</div>
          @for (opt of options; track opt.id) {
            <button
              type="button"
              class="profile-option"
              [class.active]="selected() === opt.id"
              (click)="pick(opt.id)"
            >
              <span class="profile-option-label">{{ opt.label }}</span>
              <span class="profile-option-desc">{{ opt.description }}</span>
            </button>
          }
        </div>
      }
    </div>
  `,
  styles: [`
    .profile-picker {
      position: relative;
      flex-shrink: 0;
    }
    .profile-picker.chip .profile-trigger {
      max-width: min(160px, 36vw);
      padding: 6px 12px;
      background: var(--glass-bg);
      box-shadow: var(--shadow-glass);
    }
    .profile-trigger {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 4px 10px;
      border-radius: 999px;
      border: 1px solid var(--glass-border);
      background: var(--overlay-1);
      color: var(--text-secondary);
      font-size: 12px;
      cursor: pointer;
    }
    .profile-trigger:hover {
      color: var(--text-primary);
      border-color: var(--glass-border-strong);
    }
    .profile-trigger-label {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .profile-override-dot {
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: var(--accent-warning, #e8a838);
      flex-shrink: 0;
    }
    .chevron {
      flex-shrink: 0;
      opacity: 0.6;
    }
    .profile-menu {
      position: absolute;
      bottom: calc(100% + 8px);
      left: 0;
      min-width: 280px;
      max-width: min(320px, calc(100vw - 24px));
      padding: 6px;
      border-radius: 12px;
      border: 1px solid var(--glass-border-strong);
      background: var(--glass-bg-hover);
      backdrop-filter: blur(16px);
      box-shadow: var(--shadow-glass);
      z-index: 50;
    }
    .profile-menu-title {
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--text-muted);
      padding: 6px 8px 4px;
    }
    .profile-option {
      display: flex;
      flex-direction: column;
      align-items: flex-start;
      gap: 2px;
      width: 100%;
      text-align: left;
      padding: 8px 10px;
      border: none;
      border-radius: 8px;
      background: transparent;
      color: var(--text-primary);
      cursor: pointer;
    }
    .profile-option:hover {
      background: var(--overlay-1);
    }
    .profile-option.active {
      background: var(--accent-primary-glow);
    }
    .profile-option-label {
      font-size: 13px;
      font-weight: 600;
    }
    .profile-option-desc {
      font-size: 11px;
      color: var(--text-muted);
      line-height: 1.35;
    }
  `],
})
export class ChatOrchestrationProfilePickerComponent {
  readonly profiles = inject(AgentOrchestrationProfileService);
  readonly options = ORCHESTRATION_PROFILE_OPTIONS;
  readonly open = signal(false);
  private readonly root = viewChild<ElementRef<HTMLElement>>('root');

  triggerLabel(): string {
    return this.profiles.triggerLabel(this.profiles.activeAgentId());
  }

  triggerTitle(): string {
    return this.profiles.triggerTitle(this.profiles.activeAgentId());
  }

  selected(): OrchestrationProfileChoice {
    return this.profiles.choiceFor(this.profiles.activeAgentId());
  }

  toggleOpen(): void {
    this.open.update(v => !v);
  }

  pick(id: OrchestrationProfileChoice): void {
    const agentId = this.profiles.activeAgentId();
    if (agentId) {
      this.profiles.setChoice(agentId, id);
    }
    this.open.set(false);
  }

  @HostListener('document:click')
  closeOnOutsideClick(): void {
    if (this.open()) this.open.set(false);
  }
}
