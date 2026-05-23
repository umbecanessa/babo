import { Component, Input } from '@angular/core';
import { CommonModule } from '@angular/common';

interface HormoneDisplay {
  name: string;
  key: string;
  color: string;
  value: number;
}

@Component({
  selector: 'app-hormone-panel',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './hormone-panel.component.html',
  styleUrl: './hormone-panel.component.scss',
})
export class HormonePanelComponent {
  @Input() hormones: Record<string, number> = {};

  get hormoneList(): HormoneDisplay[] {
    return [
      { name: 'Dopamine', key: 'dopamine', color: '#34d399', value: this.hormones['dopamine'] || 0 },
      { name: 'Serotonin', key: 'serotonin', color: '#38bdf8', value: this.hormones['serotonin'] || 0 },
      { name: 'Norepinephrine', key: 'norepinephrine', color: '#fbbf24', value: this.hormones['norepinephrine'] || 0 },
      { name: 'Cortisol', key: 'cortisol', color: '#f87171', value: this.hormones['cortisol'] || 0 },
      { name: 'Oxytocin', key: 'oxytocin', color: '#a78bfa', value: this.hormones['oxytocin'] || 0 },
    ];
  }
}
