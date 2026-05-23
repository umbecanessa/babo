import { Component, input, output, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';

export interface ConfigFieldSchema {
  key: string;
  type: string;
  description?: string;
  required?: boolean;
  default?: unknown;
  options?: string[];
  scope?: string;
  category?: string;
}

@Component({
  selector: 'app-schema-config-form',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    @if (groupedFields().length === 0) {
      <p class="no-schema">No configuration fields defined.</p>
    } @else {
      @for (group of groupedFields(); track group.category) {
        <div class="schema-group">
          @if (group.category) {
            <h4 class="schema-group-title">{{ group.categoryLabel }}</h4>
          }
          @for (f of group.fields; track f.key) {
            <div class="schema-field">
              <label class="schema-label">
                {{ f.key }}
                @if (f.required) {
                  <span class="required-mark">*</span>
                }
              </label>
              @if (f.description) {
                <p class="schema-description">{{ f.description }}</p>
              }
              @switch (f.type) {
                @case ('boolean') {
                  <label class="schema-toggle">
                    <input type="checkbox"
                      [checked]="getValue(f.key)"
                      (change)="onChange(f.key, $any($event.target).checked)" />
                    <span class="toggle-text">{{ getValue(f.key) ? 'On' : 'Off' }}</span>
                  </label>
                }
                @case ('secret') {
                  <div class="schema-input-wrap">
                    <input [type]="revealed()[f.key] ? 'text' : 'password'"
                      class="schema-input"
                      [value]="getValue(f.key)"
                      (input)="onChange(f.key, $any($event.target).value)"
                      [placeholder]="f.required ? '' : '(optional)'" />
                    <button type="button" class="reveal-btn" (click)="toggleReveal(f.key)" [attr.aria-label]="(revealed()[f.key] ? 'Hide' : 'Show') + ' value'">
                      {{ revealed()[f.key] ? 'Hide' : 'Show' }}
                    </button>
                  </div>
                }
                @case ('choice') {
                  <select class="schema-input schema-select"
                    [value]="getValue(f.key)"
                    (change)="onChange(f.key, $any($event.target).value)">
                    @if (f.default !== undefined && getValue(f.key) === undefined) {
                      <option value="" disabled>Select...</option>
                    }
                    @for (opt of (f.options || []); track opt) {
                      <option [value]="opt">{{ opt }}</option>
                    }
                  </select>
                }
                @case ('number') {
                  <input type="number" class="schema-input"
                    [value]="getValue(f.key)"
                    (input)="onChange(f.key, +$any($event.target).value)" />
                }
                @case ('list') {
                  <input type="text" class="schema-input"
                    [value]="formatListValue(getValue(f.key))"
                    (input)="onListInput(f.key, $any($event.target).value)"
                    placeholder="Comma-separated values" />
                }
                @default {
                  @if (isReadOnly(f)) {
                    <div class="schema-readonly">{{ formatDisplayValue(getValue(f.key)) }}</div>
                  } @else {
                    <input type="text" class="schema-input"
                      [value]="formatDisplayValue(getValue(f.key))"
                      (input)="onChange(f.key, $any($event.target).value)"
                      [placeholder]="f.required ? '' : '(optional)'" />
                  }
                }
              }
            </div>
          }
        </div>
      }
      <button type="button" class="schema-save-btn" (click)="save.emit()" [disabled]="saving()">
        @if (saving()) {
          <span class="btn-spinner"></span>
        }
        {{ saveSuccess() ? 'Saved!' : 'Save Configuration' }}
      </button>
    }
  `,
  styles: [`
    .no-schema { color: var(--text-muted, #787890); font-size: 13px; margin: 0; }
    .schema-group { margin-bottom: 20px; }
    .schema-group-title {
      font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em;
      color: var(--text-muted, #787890); margin: 0 0 10px 0; font-weight: 600;
    }
    .schema-field { margin-bottom: 14px; }
    .schema-label { display: block; font-size: 13px; font-weight: 500; color: var(--text-primary, #c5c5d0); margin-bottom: 4px; }
    .required-mark { color: #f87171; }
    .schema-description { font-size: 12px; color: var(--text-muted, #787890); margin: 0 0 6px 0; line-height: 1.4; }
    .schema-input, .schema-select {
      width: 100%; padding: 8px 12px; font-size: 13px; color: var(--text-primary, #c5c5d0);
      background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.08); border-radius: 8px;
      outline: none;
    }
    .schema-input-wrap { display: flex; gap: 8px; align-items: center; }
    .schema-input-wrap .schema-input { flex: 1; }
    .reveal-btn {
      padding: 6px 10px; font-size: 12px; color: var(--text-muted); background: transparent;
      border: 1px solid rgba(255,255,255,0.08); border-radius: 8px; cursor: pointer;
    }
    .schema-readonly {
      padding: 8px 12px; font-size: 13px; color: var(--text-muted); background: rgba(255,255,255,0.02);
      border: 1px solid rgba(255,255,255,0.06); border-radius: 8px;
    }
    .schema-toggle { display: inline-flex; align-items: center; gap: 8px; cursor: pointer; }
    .schema-toggle input { accent-color: var(--accent, #38bdf8); }
    .toggle-text { font-size: 13px; color: var(--text-primary); }
    .schema-save-btn {
      display: inline-flex; align-items: center; gap: 8px; margin-top: 16px;
      padding: 8px 18px; font-size: 13px; font-weight: 500; color: #050508;
      background: var(--accent, #38bdf8); border: none; border-radius: 8px; cursor: pointer;
    }
    .schema-save-btn:disabled { opacity: 0.6; cursor: not-allowed; }
    .btn-spinner {
      display: inline-block; width: 12px; height: 12px; border: 2px solid rgba(0,0,0,0.2);
      border-top-color: currentColor; border-radius: 50%; animation: spin 0.8s linear infinite;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
  `],
})
export class SchemaConfigFormComponent {
  schema = input.required<ConfigFieldSchema[]>();
  values = input<Record<string, unknown>>({});
  saving = input(false);
  saveSuccess = input(false);
  /** Keys that should be shown as read-only (e.g. connection info set by system) */
  readOnlyKeys = input<Set<string>>(new Set());

  configChange = output<{ key: string; value: unknown }>();
  save = output<void>();

  revealed = signal<Record<string, boolean>>({});

  private categoryOrder = ['connection', 'identity', 'policy', ''];

  groupedFields = computed(() => {
    const list = this.schema() ?? [];
    const byCategory = new Map<string, ConfigFieldSchema[]>();
    for (const f of list) {
      const cat = f.category ?? '';
      if (!byCategory.has(cat)) byCategory.set(cat, []);
      byCategory.get(cat)!.push(f);
    }
    const order = this.categoryOrder.filter(c => byCategory.has(c));
    const rest = [...byCategory.keys()].filter(c => !this.categoryOrder.includes(c));
    return [...order, ...rest].map(category => ({
      category,
      categoryLabel: category ? category.charAt(0).toUpperCase() + category.slice(1) : 'General',
      fields: byCategory.get(category) ?? [],
    })).filter(g => g.fields.length > 0);
  });

  getValue(key: string): unknown {
    const v = this.values()[key];
    return v !== undefined ? v : (this.schema().find(f => f.key === key)?.default);
  }

  formatDisplayValue(val: unknown): string {
    if (val === undefined || val === null) return '';
    if (typeof val === 'object') return JSON.stringify(val);
    return String(val);
  }

  formatListValue(val: unknown): string {
    if (Array.isArray(val)) return val.join(', ');
    if (typeof val === 'string') return val;
    return '';
  }

  isReadOnly(f: ConfigFieldSchema): boolean {
    return this.readOnlyKeys().has(f.key);
  }

  toggleReveal(key: string): void {
    this.revealed.update(prev => ({ ...prev, [key]: !prev[key] }));
  }

  onChange(key: string, value: unknown): void {
    this.configChange.emit({ key, value });
  }

  onListInput(key: string, raw: string): void {
    const value = raw.split(',').map(s => s.trim()).filter(Boolean);
    this.configChange.emit({ key, value });
  }
}
