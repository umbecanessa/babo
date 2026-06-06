import {

  Component,

  HostListener,

  inject,

  signal,

  computed,

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

        [attr.aria-label]="'Orchestration: ' + triggerLabel()"

        [title]="triggerTitle()"

      >

        <svg class="profile-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">

          <path d="M12 2L2 7l10 5 10-5-10-5z"></path>

          <path d="M2 17l10 5 10-5"></path>

          <path d="M2 12l10 5 10-5"></path>

        </svg>

        <span class="profile-trigger-label">

          <span class="profile-depth">{{ depthLabel() }}</span>

          @if (modeLabel()) {

            <span class="profile-sep"> · </span>

            <span class="profile-mode">{{ modeLabel() }}</span>

          }

        </span>

        @if (profiles.hasManualOverride()) {

          <span class="profile-override-dot" title="Manual profile override"></span>

        }

        <svg class="chevron" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">

          <polyline points="6 9 12 15 18 9"></polyline>

        </svg>

      </button>

      @if (open()) {

        <div class="context-menu-backdrop" (click)="open.set(false)"></div>

        <div class="context-menu-panel profile-menu" role="listbox" (click)="$event.stopPropagation()">

          <div class="context-menu-title">Orchestration depth</div>

          @if (modeLabel()) {

            <p class="context-menu-hint">Current mode: <strong>{{ modeLabel() }}</strong> (set by agent during task)</p>

          }

          @for (opt of options; track opt.id) {

            <button

              type="button"

              class="context-menu-item context-menu-option profile-option"

              [class.active]="selected() === opt.id"

              (click)="pick(opt.id)"

            >

              <span class="context-menu-option-label">{{ opt.label }}</span>

              <span class="context-menu-option-desc">{{ opt.description }}</span>

            </button>

          }

        </div>

      }

    </div>

  `,

  styles: [`

    :host {

      display: block;

      flex: 0 0 auto;

    }

    .profile-picker {

      position: relative;

      flex-shrink: 0;

      display: block;

      width: fit-content;

      max-width: 100%;

    }

    .profile-picker.chip .profile-trigger {

      max-width: min(260px, 48vw);

      min-width: 72px;

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

      width: 100%;

    }

    .profile-trigger:hover {

      color: var(--text-primary);

      border-color: var(--glass-border-strong);

    }

    .profile-icon {

      flex-shrink: 0;

      opacity: 0.75;

    }

    .profile-trigger-label {

      overflow: hidden;

      text-overflow: ellipsis;

      white-space: nowrap;

      min-width: 0;

    }

    .profile-depth {

      color: var(--text-secondary);

    }

    .profile-sep {

      opacity: 0.55;

    }

    .profile-mode {

      color: var(--accent-primary);

      font-weight: 500;

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
      padding: 8px;
    }

  `],

})

export class ChatOrchestrationProfilePickerComponent {

  readonly profiles = inject(AgentOrchestrationProfileService);

  readonly options = ORCHESTRATION_PROFILE_OPTIONS;

  readonly open = signal(false);



  private readonly agentId = computed(() => this.profiles.activeAgentId());



  depthLabel(): string {

    this.profiles.revision();

    return this.profiles.depthLabel(this.agentId());

  }



  modeLabel(): string | null {

    this.profiles.revision();

    return this.profiles.modeLabel(this.agentId());

  }



  triggerLabel(): string {

    this.profiles.revision();

    return this.profiles.triggerLabel(this.agentId());

  }



  triggerTitle(): string {

    this.profiles.revision();

    return this.profiles.triggerTitle(this.agentId());

  }



  selected(): OrchestrationProfileChoice {

    this.profiles.revision();

    return this.profiles.choiceFor(this.agentId());

  }



  toggleOpen(): void {

    this.open.update(v => !v);

  }



  pick(id: OrchestrationProfileChoice): void {

    const agentId = this.agentId();

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


