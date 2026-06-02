$root = Join-Path $PSScriptRoot '..\src'
$files = Get-ChildItem -Path $root -Recurse -Include *.scss,*.ts,*.html |
  Where-Object { $_.FullName -notmatch 'theme-colors\.ts|theme\.service\.ts|_theme\.scss|\\styles\.scss$' }

$replacements = @(
  @('#050508', 'var(--bg-primary)'),
  @('#0a0a0a', 'var(--bg-primary)'),
  @('#0c0c0e', 'var(--bg-primary)'),
  @('#111111', 'var(--bg-secondary)'),
  @('#141414', 'var(--bg-surface)'),
  @('#161616', 'var(--bg-secondary)'),
  @('#1a1a1a', 'var(--bg-elevated)'),
  @('#262626', 'var(--glass-border)'),
  @('#38bdf8', 'var(--accent-primary)'),
  @('#0ea5e9', 'var(--accent-primary)'),
  @('#6366f1', 'var(--accent-primary)'),
  @('#2563eb', 'var(--accent-primary)'),
  @('#818cf8', 'var(--accent-primary)'),
  @('#34d399', 'var(--accent-success)'),
  @('#fbbf24', 'var(--accent-warn)'),
  @('#f59e0b', 'var(--accent-warn)'),
  @('#f87171', 'var(--accent-danger)'),
  @('#ef4444', 'var(--accent-danger)'),
  @('#a78bfa', 'var(--accent-primary)'),
  @('#e5e5e5', 'var(--text-primary)'),
  @('#f0f0f0', 'var(--text-primary)'),
  @('#a3a3a3', 'var(--text-secondary)'),
  @('#787890', 'var(--text-muted)'),
  @('#8a8a9a', 'var(--text-muted)'),
  @('#737373', 'var(--text-muted)'),
  @('rgba(14, 14, 18, 0.95)', 'var(--glass-bg)'),
  @('rgba(14, 14, 18, 0.9)', 'var(--glass-bg)'),
  @('rgba(14, 14, 18, 0.8)', 'var(--glass-bg)'),
  @('rgba(14, 14, 18, 0.72)', 'var(--glass-bg)'),
  @('rgba(14, 14, 18, 0.7)', 'var(--glass-bg)'),
  @('rgba(14, 14, 18, 0.65)', 'var(--glass-bg)'),
  @('rgba(14, 14, 18, 0.6)', 'var(--glass-bg)'),
  @('rgba(14, 14, 18, 0.55)', 'var(--glass-bg)'),
  @('rgba(14, 14, 18, 0.5)', 'var(--glass-bg)'),
  @('rgba(14, 14, 18, 0.45)', 'var(--glass-bg)'),
  @('rgba(14, 14, 18, 0.4)', 'var(--glass-bg)'),
  @('rgba(10, 10, 14, 0.9)', 'var(--glass-bg)'),
  @('rgba(10, 10, 14, 0.85)', 'var(--glass-bg)'),
  @('rgba(56, 189, 248, 0.5)', 'var(--accent-primary-glow)'),
  @('rgba(56, 189, 248, 0.45)', 'var(--accent-primary-glow)'),
  @('rgba(56, 189, 248, 0.4)', 'var(--accent-primary-glow)'),
  @('rgba(56, 189, 248, 0.35)', 'var(--accent-primary-glow)'),
  @('rgba(56, 189, 248, 0.3)', 'var(--accent-primary-glow)'),
  @('rgba(56, 189, 248, 0.25)', 'var(--accent-primary-glow)'),
  @('rgba(56, 189, 248, 0.22)', 'var(--accent-primary-glow)'),
  @('rgba(56, 189, 248, 0.2)', 'var(--accent-primary-glow)'),
  @('rgba(56, 189, 248, 0.18)', 'var(--accent-primary-glow)'),
  @('rgba(56, 189, 248, 0.15)', 'var(--accent-primary-glow)'),
  @('rgba(56, 189, 248, 0.12)', 'var(--accent-primary-glow)'),
  @('rgba(56, 189, 248, 0.1)', 'var(--accent-primary-glow)'),
  @('rgba(56, 189, 248, 0.08)', 'var(--accent-primary-glow)'),
  @('rgba(56, 189, 248, 0.06)', 'var(--accent-primary-glow)'),
  @('rgba(56, 189, 248, 0.05)', 'var(--accent-primary-glow)'),
  @('rgba(56, 189, 248, 0.04)', 'var(--accent-primary-glow)'),
  @('rgba(56, 189, 248, 0.03)', 'var(--accent-primary-glow)'),
  @('rgba(56, 189, 248, 0.85)', 'var(--accent-primary)'),
  @('rgba(56, 189, 248, 0.7)', 'var(--accent-primary)'),
  @('rgba(56, 189, 248, 0.6)', 'var(--accent-primary)'),
  @('var(--accent, var(--accent-primary))', 'var(--accent-primary)'),
  @('var(--accent-purple, var(--accent-primary))', 'var(--accent-primary)')
)

$count = 0
foreach ($file in $files) {
  $content = [System.IO.File]::ReadAllText($file.FullName)
  $orig = $content
  foreach ($pair in $replacements) {
    $content = $content.Replace($pair[0], $pair[1])
  }
  if ($content -ne $orig) {
    [System.IO.File]::WriteAllText($file.FullName, $content)
    $count++
    Write-Host "Updated: $($file.Name)"
  }
}
Write-Host "Done. $count files updated."
