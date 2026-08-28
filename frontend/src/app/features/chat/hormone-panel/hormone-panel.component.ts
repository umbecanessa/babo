import { Component, Input } from '@angular/core';
import { CommonModule } from '@angular/common';
import { TranslateModule } from '@ngx-translate/core';

interface HormoneDisplay {
  nameKey: string;
  key: string;
  color: string;
  value: number;
}

@Component({
  selector: 'app-hormone-panel',
  standalone: true,
  imports: [CommonModule, TranslateModule],
  templateUrl: './hormone-panel.component.html',
  styleUrl: './hormone-panel.component.scss',
})
export class HormonePanelComponent {
  @Input() hormones: Record<string, number> = {};

  get hormoneList(): HormoneDisplay[] {
    return [
      { nameKey: 'info.hormones.legend.dopamine', key: 'dopamine', color: 'var(--accent-success)', value: this.hormones['dopamine'] || 0 },
      { nameKey: 'info.hormones.legend.serotonin', key: 'serotonin', color: 'var(--accent-primary)', value: this.hormones['serotonin'] || 0 },
      { nameKey: 'info.hormones.legend.norepinephrine', key: 'norepinephrine', color: 'var(--accent-warn)', value: this.hormones['norepinephrine'] || 0 },
      { nameKey: 'info.hormones.legend.cortisol', key: 'cortisol', color: 'var(--accent-danger)', value: this.hormones['cortisol'] || 0 },
      { nameKey: 'info.hormones.legend.oxytocin', key: 'oxytocin', color: 'var(--accent-primary)', value: this.hormones['oxytocin'] || 0 },
    ];
  }
}
