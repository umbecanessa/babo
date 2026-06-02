import { Pipe, PipeTransform } from '@angular/core';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import { marked, Renderer } from 'marked';
import hljs from 'highlight.js';

const renderer = new Renderer();
renderer.code = ({ text, lang }: { text: string; lang?: string }) => {
  const language = lang && hljs.getLanguage(lang) ? lang : 'plaintext';
  const highlighted = hljs.highlight(text, { language }).value;
  const escaped = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  return (
    `<div class="code-block-wrapper">` +
    `<div class="code-block-header">` +
    `<span class="code-lang">${language}</span>` +
    `<button class="copy-btn" data-code="${escaped}">Copy</button>` +
    `</div>` +
    `<pre><code class="hljs language-${language}">${highlighted}</code></pre>` +
    `</div>`
  );
};

renderer.codespan = ({ text }: { text: string }) => {
  return `<code class="inline-code">${text}</code>`;
};

// Render external links with a subtle external-link icon.
// The MessageListComponent intercepts clicks and opens them in the in-app browser.
renderer.link = ({ href, title, text }: { href: string; title?: string | null; text: string }) => {
  const safeHref = href ? href.replace(/"/g, '&quot;') : '';
  const titleAttr = title ? ` title="${title.replace(/"/g, '&quot;')}"` : '';
  const isExternal = /^https?:\/\//i.test(safeHref);
  const icon = isExternal
    ? `<svg class="md-link-icon" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>`
    : '';
  return `<a href="${safeHref}"${titleAttr}>${text}${icon}</a>`;
};

marked.setOptions({
  renderer,
  breaks: true,
  gfm: true,
});

@Pipe({
  name: 'markdown',
  standalone: true,
  pure: true,
})
export class MarkdownPipe implements PipeTransform {
  constructor(private sanitizer: DomSanitizer) {}

  transform(value: string | null | undefined): SafeHtml {
    if (!value) return '';
    const html = marked.parse(value, { async: false }) as string;
    return this.sanitizer.bypassSecurityTrustHtml(html);
  }
}
