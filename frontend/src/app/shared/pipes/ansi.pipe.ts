import { Pipe, PipeTransform } from '@angular/core';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';

const ANSI_COLORS: Record<number, string> = {
  30: '#1e1e2e', 31: '#f38ba8', 32: '#a6e3a1', 33: '#f9e2af',
  34: '#89b4fa', 35: '#cba6f7', 36: '#94e2d5', 37: '#cdd6f4',
  39: 'inherit',
  90: '#585b70', 91: '#f38ba8', 92: '#a6e3a1', 93: '#f9e2af',
  94: '#89b4fa', 95: '#cba6f7', 96: '#94e2d5', 97: '#cdd6f4',
};

const ANSI_BG: Record<number, string> = {
  40: '#1e1e2e', 41: '#f38ba8', 42: '#a6e3a1', 43: '#f9e2af',
  44: '#89b4fa', 45: '#cba6f7', 46: '#94e2d5', 47: '#cdd6f4',
  49: 'transparent',
};

function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function ansiToHtml(input: string): string {
  const re = /\x1b\[([0-9;]*)m/g;
  let result = '';
  let last = 0;
  let openSpans = 0;
  let bold = false;
  let dim = false;
  let fg = '';
  let bg = '';

  const flush = () => {
    const styles: string[] = [];
    if (bold) styles.push('font-weight:bold');
    if (dim) styles.push('opacity:0.6');
    if (fg) styles.push(`color:${fg}`);
    if (bg) styles.push(`background:${bg}`);
    if (styles.length) {
      result += `<span style="${styles.join(';')}">`;
      openSpans++;
    }
  };

  const closeAll = () => {
    while (openSpans > 0) { result += '</span>'; openSpans--; }
  };

  let m: RegExpExecArray | null;
  while ((m = re.exec(input)) !== null) {
    result += escapeHtml(input.slice(last, m.index));
    last = m.index + m[0].length;

    closeAll();

    const codes = m[1].split(';').map(Number);
    for (const c of codes) {
      if (c === 0) { bold = false; dim = false; fg = ''; bg = ''; }
      else if (c === 1) bold = true;
      else if (c === 2) dim = true;
      else if (c === 22) { bold = false; dim = false; }
      else if (ANSI_COLORS[c]) fg = ANSI_COLORS[c];
      else if (c === 39) fg = '';
      else if (ANSI_BG[c]) bg = ANSI_BG[c];
      else if (c === 49) bg = '';
    }
    flush();
  }

  result += escapeHtml(input.slice(last));
  closeAll();
  return result;
}

@Pipe({
  name: 'ansi',
  standalone: true,
  pure: true,
})
export class AnsiPipe implements PipeTransform {
  constructor(private sanitizer: DomSanitizer) {}

  transform(value: string | null | undefined): SafeHtml {
    if (!value) return '';
    return this.sanitizer.bypassSecurityTrustHtml(ansiToHtml(value));
  }
}
