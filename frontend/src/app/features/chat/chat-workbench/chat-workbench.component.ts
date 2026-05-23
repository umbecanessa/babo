import { Component, ElementRef, computed, effect, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import {
  ChatWorkbenchService,
  WorkbenchEntry,
  WorkbenchLane,
} from '../../../core/services/chat-workbench.service';
import { AnsiPipe } from '../../../shared/pipes/ansi.pipe';

export type WorkbenchTab = 'all' | 'chat' | 'background';

@Component({
  selector: 'app-chat-workbench',
  standalone: true,
  imports: [CommonModule, AnsiPipe],
  templateUrl: './chat-workbench.component.html',
  styleUrl: './chat-workbench.component.scss',
})
export class ChatWorkbenchComponent {
  private readonly host = inject(ElementRef<HTMLElement>);
  readonly workbench = inject(ChatWorkbenchService);

  /** Which lane filter is active */
  readonly activeTab = signal<WorkbenchTab>('all');

  private prevFocusKey: string | null = null;

  readonly counts = computed(() => {
    const list = this.workbench.entries();
    let chat = 0;
    let background = 0;
    for (const e of list) {
      if (e.lane === 'background') {
        background++;
      } else {
        chat++;
      }
    }
    return { all: list.length, chat, background };
  });

  /** Newest first, filtered by tab */
  readonly visibleEntries = computed(() => {
    const list = this.workbench.entries();
    const sorted = [...list].sort((a, b) => b.ts - a.ts);
    const tab = this.activeTab();
    if (tab === 'all') {
      return sorted;
    }
    const lane: WorkbenchLane = tab === 'background' ? 'background' : 'chat';
    return sorted.filter((e) => e.lane === lane);
  });

  constructor() {
    effect(() => {
      const key = this.workbench.focusKey();
      const list = this.workbench.entries();

      if (key == null || key === '') {
        this.prevFocusKey = null;
        return;
      }

      if (key !== this.prevFocusKey) {
        this.prevFocusKey = key;
        for (let i = list.length - 1; i >= 0; i--) {
          if (list[i].correlationKey === key) {
            this.activeTab.set(list[i].lane === 'background' ? 'background' : 'chat');
            break;
          }
        }
      }

      queueMicrotask(() => this.scrollToCorrelation(key));
    });
  }

  setTab(tab: WorkbenchTab): void {
    this.activeTab.set(tab);
  }

  trackEntry(_i: number, e: WorkbenchEntry): string {
    return e.id;
  }

  close(): void {
    this.workbench.closePanel();
  }

  kindLabel(kind: string): string {
    switch (kind) {
      case 'agentic':
        return 'Task';
      case 'tool':
        return 'Tool';
      case 'activity':
        return 'Activity';
      default:
        return 'Log';
    }
  }

  private scrollToCorrelation(key: string): void {
    const root = this.host.nativeElement;
    const rows = root.querySelectorAll('[data-wb-corr]') as NodeListOf<HTMLElement>;
    let target: HTMLElement | null = null;
    for (let i = 0; i < rows.length; i++) {
      const el = rows.item(i);
      if (el.dataset['wbCorr'] === key) {
        target = el;
        break;
      }
    }
    if (target) {
      target.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
  }
}
