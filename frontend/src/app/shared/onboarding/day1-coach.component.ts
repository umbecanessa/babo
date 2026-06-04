import {

  Component,

  DestroyRef,

  ElementRef,

  afterNextRender,

  effect,

  inject,

  signal,

} from '@angular/core';

import { CommonModule } from '@angular/common';

import { RouterLink } from '@angular/router';

import { Day1CoachService, Day1CoachStep } from './day1-coach.service';



interface CoachLayout {

  highlight: { top: number; left: number; width: number; height: number };

  card: { top: number; left: number };

  placement: 'above' | 'below';

}



const STEP_ANCHORS: Record<Day1CoachStep, string> = {

  chat: '[data-day1-coach="chat-composer"]',

  thinking: '[data-day1-coach="settings"]',

  skills: '[data-day1-coach="tools"]',

};



const TARGET_CLASS = 'day1-coach-target';



function unionRect(elements: Element[]): DOMRect | null {

  let top = Infinity;

  let left = Infinity;

  let right = -Infinity;

  let bottom = -Infinity;

  for (const el of elements) {

    const r = el.getBoundingClientRect();

    if (r.width <= 0 && r.height <= 0) continue;

    top = Math.min(top, r.top);

    left = Math.min(left, r.left);

    right = Math.max(right, r.right);

    bottom = Math.max(bottom, r.bottom);

  }

  if (!Number.isFinite(top)) return null;

  return new DOMRect(left, top, right - left, bottom - top);

}



@Component({

  selector: 'app-day1-coach',

  standalone: true,

  imports: [CommonModule, RouterLink],

  template: `

    @if (coach.activeStep(); as step) {

      @if (layout(); as L) {

        <div

          class="coach-spotlight"

          [style.top.px]="L.highlight.top"

          [style.left.px]="L.highlight.left"

          [style.width.px]="L.highlight.width"

          [style.height.px]="L.highlight.height"

          aria-hidden="true"

        ></div>

        <div

          class="coach-card"

          [class.above]="L.placement === 'above'"

          [class.below]="L.placement === 'below'"

          [style.top.px]="L.card.top"

          [style.left.px]="L.card.left"

          (click)="$event.stopPropagation()"

          role="dialog"

          aria-modal="true"

          [attr.aria-label]="stepTitle(step)"

        >

          @switch (step) {

            @case ('chat') {

              <p class="coach-pointer">↑ Your chat</p>

              <h3>Talk to your agent here</h3>

              <p>

                Messages go to <strong>{{ agentDisplayName() }}</strong> on this computer.

                Babo keeps running even when the window is in the background.

              </p>

            }

            @case ('thinking') {

              <p class="coach-pointer">{{ L.placement === 'below' ? '↑' : '↓' }} Settings</p>

              <h3>Models &amp; Babo Cloud</h3>

              <p>

                Open

                <a routerLink="/settings" [queryParams]="{ section: 'models' }" (click)="coach.skip()">Settings → Models &amp; AI</a>

                for your default model and sub-agents. Use the model menu in chat to pick a different model for one message.

              </p>

            }

            @case ('skills') {

              <p class="coach-pointer">↑ Tools tab</p>

              <h3>Add skills when you are ready</h3>

              <p>

                Use the <strong>Tools</strong> tab above to connect email, browser automation,

                and more.

              </p>

            }

          }

          <div class="coach-actions">

            <button type="button" class="ghost" (click)="coach.skip()">Skip tour</button>

            <button type="button" class="primary" (click)="coach.advance()">

              {{ step === 'skills' ? 'Done' : 'Next' }}

            </button>

          </div>

        </div>

      }

    }

  `,

  styles: [

    `

      :host {

        --coach-scrim: var(--backdrop-scrim);

        position: fixed;

        inset: 0;

        z-index: var(--modal-z-coach, 12000);

        pointer-events: none;

      }

      .coach-spotlight {

        position: fixed;

        z-index: 1;

        border-radius: 12px;

        background: transparent;

        box-shadow:

          0 0 0 3px var(--accent-primary),

          0 0 0 9999px var(--coach-scrim);

        pointer-events: none;

      }

      .coach-card {

        position: fixed;

        z-index: 2;

        max-width: min(380px, calc(100vw - 32px));

        background: var(--modal-bg);

        border: 1px solid var(--glass-border);

        border-radius: var(--radius-lg);

        padding: 18px 20px;

        box-shadow: var(--shadow-glass), 0 24px 80px rgba(0, 0, 0, 0.35);

        color: var(--text-primary);

        pointer-events: auto;

      }

      .coach-pointer {

        margin: 0 0 6px;

        font-size: 11px;

        font-weight: 600;

        letter-spacing: 0.06em;

        text-transform: uppercase;

        color: var(--accent-primary);

      }

      h3 {

        margin: 0 0 8px;

        font-size: 17px;

        font-weight: 500;

      }

      p {

        margin: 0;

        font-size: 14px;

        line-height: 1.55;

        color: var(--text-secondary);

      }

      a {

        color: var(--accent-primary);

        font-weight: 500;

      }

      .coach-actions {

        display: flex;

        justify-content: flex-end;

        gap: 10px;

        margin-top: 18px;

      }

      button {

        border-radius: 999px;

        padding: 8px 16px;

        font-size: 13px;

        cursor: pointer;

      }

      .ghost {

        background: transparent;

        border: none;

        color: var(--text-secondary);

      }

      .primary {

        background: var(--accent-tint-bg);

        color: var(--on-accent-tint);

        border: 1px solid var(--accent-tint-border);

      }

      :global(.day1-coach-target) {

        position: relative;

        z-index: calc(var(--modal-z-coach, 12000) + 2);

      }

    `,

  ],

})

export class Day1CoachComponent {

  readonly coach = inject(Day1CoachService);

  readonly layout = signal<CoachLayout | null>(null);

  readonly agentDisplayName = signal('your agent');



  private readonly destroyRef = inject(DestroyRef);

  private readonly hostRef = inject(ElementRef<HTMLElement>);

  private anchoredEl: Element | null = null;

  private retryTimer: ReturnType<typeof setTimeout> | null = null;

  private retryAttempts = 0;



  constructor() {

    afterNextRender(() => {

      document.body.appendChild(this.hostRef.nativeElement);

    });



    this.destroyRef.onDestroy(() => {

      this.clearTargetClass();

      this.clearRetry();

      this.hostRef.nativeElement.remove();

    });



    effect(() => {

      const step = this.coach.activeStep();

      const _epoch = this.coach.layoutEpoch();

      void _epoch;

      this.clearTargetClass();

      this.clearRetry();

      if (!step) {

        this.layout.set(null);

        return;

      }

      const header = document.querySelector('[data-day1-coach="agent-header"]');

      const name = header?.querySelector('.agent-name')?.textContent?.trim();

      if (name) {

        this.agentDisplayName.set(name);

      }

      this.scheduleReposition(step, true);

    });



    const onLayout = () => {

      const step = this.coach.activeStep();

      if (step) {

        this.reposition(step);

      }

    };

    window.addEventListener('resize', onLayout);

    window.addEventListener('scroll', onLayout, true);

    this.destroyRef.onDestroy(() => {

      window.removeEventListener('resize', onLayout);

      window.removeEventListener('scroll', onLayout, true);

    });

  }



  stepTitle(step: Day1CoachStep): string {

    switch (step) {

      case 'chat':

        return 'Chat tour step';

      case 'thinking':

        return 'Settings tour step';

      case 'skills':

        return 'Tools tour step';

    }

  }



  private scheduleReposition(step: Day1CoachStep, resetAttempts: boolean): void {

    if (resetAttempts) {

      this.retryAttempts = 0;

    }

    requestAnimationFrame(() => {

      requestAnimationFrame(() => this.reposition(step));

    });

  }



  private clearRetry(): void {

    if (this.retryTimer !== null) {

      clearTimeout(this.retryTimer);

      this.retryTimer = null;

    }

  }



  private reposition(step: Day1CoachStep): void {

    const selector = STEP_ANCHORS[step];

    const el = document.querySelector(selector);

    const cardW = Math.min(380, window.innerWidth - 32);

    const cardH = 220;

    const pad = 10;

    const gap = 14;

    const vw = window.innerWidth;

    const vh = window.innerHeight;



    if (!el) {

      this.layout.set(null);

      if (this.retryAttempts < 40) {

        this.retryAttempts += 1;

        this.retryTimer = setTimeout(

          () => this.scheduleReposition(step, false),

          50,

        );

      }

      return;

    }



    this.retryAttempts = 0;

    el.classList.add(TARGET_CLASS);

    this.anchoredEl = el;



    const r =

      step === 'chat'

        ? unionRect([el]) ?? el.getBoundingClientRect()

        : el.getBoundingClientRect();



    if (r.width < 8 || r.height < 8) {

      this.layout.set(null);

      if (this.retryAttempts < 40) {

        this.retryAttempts += 1;

        this.retryTimer = setTimeout(

          () => this.scheduleReposition(step, false),

          50,

        );

      }

      return;

    }



    const highlight = {

      top: Math.max(8, r.top - pad),

      left: Math.max(8, r.left - pad),

      width: Math.min(vw - 16, r.width + pad * 2),

      height: Math.min(vh - 16, r.height + pad * 2),

    };



    let placement: 'above' | 'below' = 'above';

    let cardTop = highlight.top - cardH - gap;

    if (cardTop < 16) {

      placement = 'below';

      cardTop = highlight.top + highlight.height + gap;

    }

    cardTop = Math.max(16, Math.min(cardTop, vh - cardH - 16));



    let cardLeft = highlight.left + highlight.width / 2 - cardW / 2;

    cardLeft = Math.max(16, Math.min(cardLeft, vw - cardW - 16));



    this.layout.set({ highlight, card: { top: cardTop, left: cardLeft }, placement });

  }



  private clearTargetClass(): void {

    this.anchoredEl?.classList.remove(TARGET_CLASS);

    this.anchoredEl = null;

  }

}


