/**
 * Supported UI / environment languages.
 * UI locale and agent Cryptex seed share these codes; reply language still
 * follows the user message at runtime.
 */

export type AppLanguage = 'en' | 'it' | 'fr' | 'es' | 'de';

/** Picker choice: follow OS/device, or lock a language. */
export type LocalePickerChoice = 'auto' | AppLanguage;

export type LocaleSource = 'device' | 'manual';

export interface AppLocalePreference {
  language: AppLanguage;
  source: LocaleSource;
}

export interface AppLanguageOption {
  code: AppLanguage;
  /** English label for settings */
  label: string;
  /** Native label shown in the picker */
  nativeLabel: string;
}

export const APP_LANGUAGE_OPTIONS: AppLanguageOption[] = [
  { code: 'en', label: 'English', nativeLabel: 'English' },
  { code: 'it', label: 'Italian', nativeLabel: 'Italiano' },
  { code: 'fr', label: 'French', nativeLabel: 'Français' },
  { code: 'es', label: 'Spanish', nativeLabel: 'Español' },
  { code: 'de', label: 'German', nativeLabel: 'Deutsch' },
];

export const APP_LANGUAGES: AppLanguage[] = APP_LANGUAGE_OPTIONS.map((o) => o.code);

/** Order shown in settings / onboarding: Auto first, then languages. */
export const LOCALE_PICKER_CHOICES: LocalePickerChoice[] = [
  'auto',
  ...APP_LANGUAGES,
];

const STORAGE_KEY = 'babo-ui-locale';

export function normalizeLanguage(raw: string | null | undefined): AppLanguage {
  const s = (raw || '').trim().toLowerCase().replace('_', '-');
  if (!s) return 'en';
  const primary = s.split('-')[0] || s;
  if ((APP_LANGUAGES as string[]).includes(primary)) {
    return primary as AppLanguage;
  }
  return 'en';
}

/** Browser / renderer device language. */
export function detectDeviceLanguage(
  languages: readonly string[] = typeof navigator !== 'undefined'
    ? navigator.languages?.length
      ? navigator.languages
      : [navigator.language]
    : ['en'],
): AppLanguage {
  for (const tag of languages) {
    const primary = (tag || '').trim().toLowerCase().split(/[-_]/)[0];
    if ((APP_LANGUAGES as string[]).includes(primary)) {
      return primary as AppLanguage;
    }
  }
  return 'en';
}

export function languageNativeLabel(code: AppLanguage): string {
  return APP_LANGUAGE_OPTIONS.find((o) => o.code === code)?.nativeLabel ?? code;
}

export function readStoredLocale(): AppLocalePreference | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<AppLocalePreference>;
    if (!parsed?.language) return null;
    return {
      language: normalizeLanguage(parsed.language),
      source: parsed.source === 'manual' ? 'manual' : 'device',
    };
  } catch {
    return null;
  }
}

export function writeStoredLocale(pref: AppLocalePreference): void {
  localStorage.setItem(
    STORAGE_KEY,
    JSON.stringify({
      language: normalizeLanguage(pref.language),
      source: pref.source === 'manual' ? 'manual' : 'device',
    }),
  );
}

/** Resolve effective preference: stored → device (Auto). */
export function resolveLocalePreference(
  deviceLang: AppLanguage = detectDeviceLanguage(),
): AppLocalePreference {
  const stored = readStoredLocale();
  if (stored) {
    if (stored.source === 'device') {
      // Auto: re-resolve from current device each session
      return { language: deviceLang, source: 'device' };
    }
    return stored;
  }
  return { language: deviceLang, source: 'device' };
}
