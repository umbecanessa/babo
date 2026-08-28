/**
 * Unit tests for UI locale helpers (no Angular DI).
 */
import {
  detectDeviceLanguage,
  normalizeLanguage,
  resolveLocalePreference,
  LOCALE_PICKER_CHOICES,
} from './app-locale.util';

describe('app-locale.util', () => {
  it('normalizes BCP-47 tags to supported primary codes', () => {
    expect(normalizeLanguage('it-IT')).toBe('it');
    expect(normalizeLanguage('fr_FR')).toBe('fr');
    expect(normalizeLanguage('es-MX')).toBe('es');
    expect(normalizeLanguage('de')).toBe('de');
    expect(normalizeLanguage('en-GB')).toBe('en');
    expect(normalizeLanguage('ja-JP')).toBe('en');
    expect(normalizeLanguage('')).toBe('en');
  });

  it('detects first supported language from navigator list', () => {
    expect(detectDeviceLanguage(['it-IT', 'en-US'])).toBe('it');
    expect(detectDeviceLanguage(['ja', 'fr-FR'])).toBe('fr');
    expect(detectDeviceLanguage(['zh-CN'])).toBe('en');
  });

  it('picker choices start with auto then languages', () => {
    expect(LOCALE_PICKER_CHOICES[0]).toBe('auto');
    expect(LOCALE_PICKER_CHOICES).toContain('it');
    expect(LOCALE_PICKER_CHOICES).toContain('en');
  });

  it('resolveLocalePreference with device source re-resolves from device', () => {
    const key = 'babo-ui-locale';
    localStorage.setItem(key, JSON.stringify({ language: 'en', source: 'device' }));
    expect(resolveLocalePreference('it')).toEqual({ language: 'it', source: 'device' });
    localStorage.setItem(key, JSON.stringify({ language: 'fr', source: 'manual' }));
    expect(resolveLocalePreference('it')).toEqual({ language: 'fr', source: 'manual' });
    localStorage.removeItem(key);
  });
});
