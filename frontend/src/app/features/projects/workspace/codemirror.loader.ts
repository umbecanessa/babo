import { Extension } from '@codemirror/state';
import { EditorView } from '@codemirror/view';
import {
  LanguageDescription,
  syntaxHighlighting,
  HighlightStyle,
} from '@codemirror/language';
import { languages } from '@codemirror/language-data';
import { tags } from '@lezer/highlight';

const languageCache = new Map<string, Extension>();

/** Load syntax support for a workspace tab (cached). */
export async function languageExtension(
  fileName: string,
  langId: string,
): Promise<Extension> {
  const key = `${fileName}\0${langId}`;
  const cached = languageCache.get(key);
  if (cached !== undefined) return cached;

  const byFile = LanguageDescription.matchFilename(languages, fileName);
  const byName = languages.find(
    (l) => l.name === langId || l.alias.includes(langId),
  );
  const desc = byFile ?? byName;
  const ext: Extension = desc ? ((await desc.load()) ?? []) : [];
  languageCache.set(key, ext);
  return ext;
}

const baboDarkHighlight = HighlightStyle.define([
  { tag: tags.comment, color: '#6f7591', fontStyle: 'italic' },
  { tag: tags.string, color: '#14b8a6' },
  { tag: tags.keyword, color: '#c084fc' },
  { tag: tags.number, color: '#e5a520' },
  { tag: tags.typeName, color: '#7c5bf5' },
  { tag: tags.function(tags.variableName), color: '#a5b4fc' },
  { tag: tags.variableName, color: '#e8eaf2' },
  { tag: tags.propertyName, color: '#c4b5fd' },
]);

const baboLightHighlight = HighlightStyle.define([
  { tag: tags.comment, color: '#8b90a8', fontStyle: 'italic' },
  { tag: tags.string, color: '#0d9488' },
  { tag: tags.keyword, color: '#7c3aed' },
  { tag: tags.number, color: '#b45309' },
  { tag: tags.typeName, color: '#6d28d9' },
  { tag: tags.function(tags.variableName), color: '#5b21b6' },
  { tag: tags.variableName, color: '#1a1d2e' },
  { tag: tags.propertyName, color: '#6d28d9' },
]);

/** Editor chrome + Lezer colors aligned with the former Monaco babo themes. */
export function baboTheme(dark: boolean): Extension {
  return [
    EditorView.theme(
      {
        '&': {
          height: '100%',
          backgroundColor: dark ? '#15171f' : '#e4e7f2',
          color: dark ? '#e8eaf2' : '#1a1d2e',
        },
        '.cm-scroller': {
          overflow: 'auto',
          fontFamily:
            "'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace",
          fontSize: '13px',
          lineHeight: '1.54',
        },
        '.cm-content': {
          caretColor: '#7c5bf5',
          padding: '12px 0',
        },
        '.cm-cursor, .cm-dropCursor': { borderLeftColor: '#7c5bf5' },
        '&.cm-focused .cm-selectionBackground, .cm-selectionBackground': {
          backgroundColor: dark ? '#7c5bf544' : '#7c5bf533',
        },
        '.cm-activeLine': {
          backgroundColor: dark ? '#ffffff08' : '#00000006',
        },
        '.cm-gutters': {
          backgroundColor: dark ? '#12141c' : '#dce0ec',
          color: dark ? '#6f7591' : '#8b90a8',
          border: 'none',
        },
        '.cm-activeLineGutter': {
          backgroundColor: dark ? '#ffffff08' : '#00000006',
          color: dark ? '#b3b8cb' : '#4a4f6a',
        },
        '.cm-lineNumbers .cm-gutterElement': { minWidth: '2.5em' },
      },
      { dark },
    ),
    syntaxHighlighting(dark ? baboDarkHighlight : baboLightHighlight),
  ];
}
