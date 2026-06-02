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
      { name: 'Dopamine', key: 'dopamine', color: 'var(--accent-success)', value: this.hormones['dopamine'] || 0 },
      { name: 'Serotonin', key: 'serotonin', color: 'var(--accent-primary)', value: this.hormones['serotonin'] || 0 },
      { name: 'Norepinephrine', key: 'norepinephrine', color: 'var(--accent-warn)', value: this.hormones['norepinephrine'] || 0 },
      { name: 'Cortisol', key: 'cortisol', color: 'var(--accent-danger)', value: this.hormones['cortisol'] || 0 },
      { name: 'Oxytocin', key: 'oxytocin', color: 'var(--accent-primary)', value: this.hormones['oxytocin'] || 0 },
    ];
  }
}
