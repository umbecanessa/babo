import { Injectable, inject, signal } from '@angular/core';
import { TranslateService } from '@ngx-translate/core';
import { PlatformService } from '../services/platform.service';
import {
  APP_LANGUAGE_OPTIONS,
  AppLanguage,
  AppLocalePreference,
  LOCALE_PICKER_CHOICES,
  LocalePickerChoice,
  LocaleSource,
  detectDeviceLanguage,
  languageNativeLabel,
  normalizeLanguage,
  readStoredLocale,
  resolveLocalePreference,
  writeStoredLocale,
} from './app-locale.util';

@Injectable({ providedIn: 'root' })
export class LocaleService {
  private readonly translate = inject(TranslateService);
  private readonly platform = inject(PlatformService);

  /** Resolved UI language (never 'auto' — Auto resolves to a concrete code). */
  readonly language = signal<AppLanguage>('en');
  /** device = Auto; manual = user locked a language. */
  readonly source = signal<LocaleSource>('device');
  readonly options = APP_LANGUAGE_OPTIONS;
  readonly pickerChoices = LOCALE_PICKER_CHOICES;

  /** Call once at app bootstrap. */
  async init(): Promise<void> {
    const device = await this.deviceLanguage();
    let pref = resolveLocalePreference(device);

    if (this.platform.isElectron) {
      try {
        const nls = (window as any).nls;
        const cfg = await nls?.config?.get?.();
        const lang = cfg?.locale?.language;
        if (typeof lang === 'string' && lang.trim()) {
          const desktopSource: LocaleSource =
            cfg?.locale?.source === 'manual' ? 'manual' : 'device';
          const stored = readStoredLocale();
          if (!stored) {
            pref =
              desktopSource === 'manual'
                ? { language: normalizeLanguage(lang), source: 'manual' }
                : { language: device, source: 'device' };
          } else if (desktopSource === 'manual' && stored.source !== 'manual') {
            pref = { language: normalizeLanguage(lang), source: 'manual' };
          }
        }
      } catch {
        /* fall through */
      }
    }

    await this.apply(pref, { persistElectron: true });
  }

  async setLanguage(code: AppLanguage, source: LocaleSource = 'manual'): Promise<void> {
    await this.apply({ language: normalizeLanguage(code), source }, { persistElectron: true });
  }

  /** Follow OS / device language (Auto). */
  async useDeviceLanguage(): Promise<void> {
    const language = await this.deviceLanguage();
    await this.apply({ language, source: 'device' }, { persistElectron: true });
  }

  async selectPicker(choice: LocalePickerChoice): Promise<void> {
    if (choice === 'auto') {
      await this.useDeviceLanguage();
      return;
    }
    await this.setLanguage(choice, 'manual');
  }

  isPickerActive(choice: LocalePickerChoice): boolean {
    if (choice === 'auto') return this.source() === 'device';
    return this.source() === 'manual' && this.language() === choice;
  }

  isAuto(): boolean {
    return this.source() === 'device';
  }

  nativeLabel(code: AppLanguage = this.language()): string {
    return languageNativeLabel(code);
  }

  /** Native label for a picker chip (Auto uses i18n). */
  pickerNativeLabel(choice: LocalePickerChoice): string {
    if (choice === 'auto') {
      return this.translate.instant('locale.auto');
    }
    return languageNativeLabel(choice);
  }

  private async deviceLanguage(): Promise<AppLanguage> {
    if (this.platform.isElectron) {
      try {
        const nls = (window as any).nls;
        const tag = await nls?.app?.getLocale?.();
        if (typeof tag === 'string' && tag.trim()) {
          return normalizeLanguage(tag);
        }
      } catch {
        /* fall through */
      }
    }
    return detectDeviceLanguage();
  }

  private async apply(
    pref: AppLocalePreference,
    opts: { persistElectron: boolean },
  ): Promise<void> {
    const language = normalizeLanguage(pref.language);
    const source: LocaleSource = pref.source === 'manual' ? 'manual' : 'device';
    this.language.set(language);
    this.source.set(source);
    writeStoredLocale({ language, source });
    document.documentElement.lang = language;
    this.translate.use(language);

    if (opts.persistElectron && this.platform.isElectron) {
      try {
        const nls = (window as any).nls;
        await nls?.config?.set?.({
          locale: { language, source },
        });
      } catch {
        /* non-fatal */
      }
    }
  }

  /** Hydrate from Electron config when present (overrides empty localStorage). */
  async hydrateFromDesktopConfig(): Promise<void> {
    if (!this.platform.isElectron) return;
    try {
      const nls = (window as any).nls;
      const cfg = await nls?.config?.get?.();
      const lang = cfg?.locale?.language;
      if (typeof lang === 'string' && lang.trim()) {
        const source: LocaleSource = cfg?.locale?.source === 'manual' ? 'manual' : 'device';
        const stored = readStoredLocale();
        if (!stored || stored.source === 'device') {
          const device = await this.deviceLanguage();
          await this.apply(
            source === 'device'
              ? { language: device, source: 'device' }
              : { language: normalizeLanguage(lang), source: 'manual' },
            { persistElectron: false },
          );
        }
      }
    } catch {
      /* ignore */
    }
  }
}
