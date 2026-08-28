import {
  Component,
  Input,
  Output,
  EventEmitter,
  OnChanges,
  SimpleChanges,
  inject,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { TranslateModule, TranslateService } from '@ngx-translate/core';
import { CryptexRingStatus, CryptexSlotDetail } from '../../../core/models/agent.model';

export interface SlotPreview {
  content: string;
  accessIcon: string;
  salience: number;
}

export interface RingCard {
  ringId: string;
  category: string;
  displayName: string;
  color: string;
  colorRgb: string;
  slotCount: number;
  maxSlots: number;
  activePosition: string;
  allPositions: string[];
  positionIndex: number;
  previews: SlotPreview[];
  isRotating: boolean;
}

export interface CategoryGroup {
  category: string;
  label: string;
  cards: RingCard[];
}

const CATEGORY_COLORS: Record<string, string> = {
  fixed: 'var(--accent-primary)',
  project: 'var(--accent-success)',
  domain: 'var(--accent-warn)',
};

const RING_COLOR_OVERRIDES: Record<string, string> = {
  behavioral: '#c084fc',
  environment: 'var(--accent-primary)',
};

const CATEGORY_ORDER = ['fixed', 'project', 'domain'];
const CATEGORY_LABEL_KEYS: Record<string, string> = {
  fixed: 'cryptex.categories.fixed',
  project: 'cryptex.categories.project',
  domain: 'cryptex.categories.domain',
};

const ACCESS_ICONS: Record<string, string> = {
  genesis: '\u{1F512}',
  system: '\u2699\uFE0F',
  malleable: '\u270F\uFE0F',
  session: '\u{1F552}',
};

function hexToRgb(hex: string): string {
  const h = hex.replace('#', '');
  const r = parseInt(h.substring(0, 2), 16);
  const g = parseInt(h.substring(2, 4), 16);
  const b = parseInt(h.substring(4, 6), 16);
  return `${r}, ${g}, ${b}`;
}

function truncate(s: string, maxLen: number): string {
  if (!s) return '';
  const single = s.replace(/\n/g, ' ').trim();
  return single.length > maxLen ? single.slice(0, maxLen) + '\u2026' : single;
}

@Component({
  selector: 'app-cryptex-viz',
  standalone: true,
  imports: [CommonModule, TranslateModule],
  templateUrl: './cryptex-viz.component.html',
  styleUrl: './cryptex-viz.component.scss',
})
export class CryptexVizComponent implements OnChanges {
  @Input() rings: CryptexRingStatus[] = [];
  @Input() activeProject: string = '';
  @Input() selectedRingId: string | null = null;

  @Output() ringSelect = new EventEmitter<string>();

  groups: CategoryGroup[] = [];

  private readonly translate = inject(TranslateService);

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['rings'] || changes['activeProject'] || changes['selectedRingId']) {
      this.buildGroups();
    }
  }

  private buildGroups(): void {
    if (!this.rings || this.rings.length === 0) {
      this.groups = [];
      return;
    }

    const grouped: Record<string, RingCard[]> = {};

    for (const ring of this.rings) {
      const cat = ring.category || 'fixed';
      const color =
        RING_COLOR_OVERRIDES[ring.ring_id] ||
        CATEGORY_COLORS[cat] ||
        '#64748b';

      const slotCount = Object.values(ring.positions || {}).reduce(
        (a, b) => a + b,
        0,
      );

      const allPositions = ring.slot_details
        ? Object.keys(ring.slot_details)
        : Object.keys(ring.positions || {});
      if (allPositions.length === 0 && ring.active_position) {
        allPositions.push(ring.active_position);
      }

      const activePos = ring.active_position || allPositions[0] || 'general';
      const positionIndex = Math.max(0, allPositions.indexOf(activePos));

      const previews = this.buildPreviews(ring, activePos);

      const card: RingCard = {
        ringId: ring.ring_id,
        category: cat,
        displayName: ring.display_name,
        color,
        colorRgb: hexToRgb(color),
        slotCount,
        maxSlots: ring.max_slots || 12,
        activePosition: activePos,
        allPositions,
        positionIndex,
        previews,
        isRotating: cat === 'project' || cat === 'domain',
      };

      if (!grouped[cat]) grouped[cat] = [];
      grouped[cat].push(card);
    }

    this.groups = CATEGORY_ORDER
      .filter((cat) => grouped[cat]?.length)
      .map((cat) => ({
        category: cat,
        label: this.translate.instant(
          CATEGORY_LABEL_KEYS[cat] || 'cryptex.categories.fixed',
        ),
        cards: grouped[cat],
      }));
  }

  private buildPreviews(ring: CryptexRingStatus, activePos: string): SlotPreview[] {
    const details: CryptexSlotDetail[] | undefined = ring.slot_details?.[activePos];
    if (!details || details.length === 0) return [];

    const sorted = [...details].sort((a, b) => (b.salience ?? 0.5) - (a.salience ?? 0.5));
    return sorted.slice(0, 3).map((slot) => ({
      content: truncate(slot.content, 50),
      accessIcon: ACCESS_ICONS[slot.access] || ACCESS_ICONS['malleable'],
      salience: slot.salience ?? 0.5,
    }));
  }

  tumblerOffset(card: RingCard): string {
    return `translateY(${card.positionIndex * -18}px)`;
  }

  onCardClick(card: RingCard): void {
    this.ringSelect.emit(card.ringId);
  }

  isSelected(card: RingCard): boolean {
    return this.selectedRingId === card.ringId;
  }

  previewOpacity(salience: number): number {
    return Math.max(0.4, Math.min(1.0, salience));
  }

  trackByCategory(_: number, group: CategoryGroup): string {
    return group.category;
  }

  trackByRingId(_: number, card: RingCard): string {
    return card.ringId;
  }

  trackByIndex(index: number): number {
    return index;
  }
}
