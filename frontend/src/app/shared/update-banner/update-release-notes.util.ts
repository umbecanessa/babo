/** Normalize GitHub / electron-updater release notes for display in the UI. */
export function normalizeUpdateReleaseNotes(raw: string): string {
  const trimmed = raw.trim();
  if (!trimmed) return '';

  // Auto-generated GitHub compare link — replace noisy HTML with readable copy.
  if (/Full\s+Changelog/i.test(trimmed) && /<a\b/i.test(trimmed)) {
    const hrefMatch = trimmed.match(/href="([^"]+)"/i);
    const ttMatch = trimmed.match(/<tt[^>]*>([^<]*)<\/tt>/i);
    const range = ttMatch?.[1]?.trim();
    const href = hrefMatch?.[1];
    if (href && range) {
      return (
        `<p>See what changed between <strong>${escapeHtml(range)}</strong>.</p>` +
        `<p><a href="${escapeAttr(href)}" rel="noopener noreferrer" target="_blank">View changelog on GitHub</a></p>`
      );
    }
    return '<p>Includes fixes and improvements from the latest release.</p>';
  }

  return trimmed;
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function escapeAttr(value: string): string {
  return escapeHtml(value).replace(/'/g, '&#39;');
}

/** Plain text for compact UI (update banner panel). */
export function updateReleaseNotesPlainText(raw: string): string {
  const html = normalizeUpdateReleaseNotes(raw);
  const text = html
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<\/p>/gi, '\n')
    .replace(/<[^>]+>/g, '')
    .replace(/&nbsp;/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/\n{2,}/g, '\n')
    .trim();
  return text;
}
