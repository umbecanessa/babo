$root = Join-Path $PSScriptRoot '..\src'
$files = Get-ChildItem -Path $root -Recurse -Include *.scss,*.ts |
  Where-Object { $_.FullName -notmatch 'theme-colors\.ts|styles\.scss$' }

$replacements = @(
  @('background: var(--bg-primary);', 'background: transparent;'),
  @('#1a1a2e', 'var(--bg-secondary)'),
  @('#1e1e2e', 'var(--bg-secondary)'),
  @('#1a1a22', 'var(--bg-secondary)'),
  @('#161620', 'var(--bg-secondary)'),
  @('#0d0d12', 'var(--bg-secondary)'),
  @('#6ee7b7', 'var(--accent-success)'),
  @('#fb923c', 'var(--accent-warn)'),
  @('#10b981', 'var(--accent-success)'),
  @('#f472b6', 'var(--accent-primary)'),
  @('#9ca3af', 'var(--text-muted)'),
  @('#ec4899', 'var(--accent-primary)'),
  @('#b0b0c0', 'var(--text-secondary)'),
  @('#e0e0f0', 'var(--text-primary)'),
  @('#c0c0cc', 'var(--text-secondary)'),
  @('rgba(14, 16, 20, 0.5)', 'var(--glass-bg)'),
  @('rgba(56, 189, 248, 0.38)', 'var(--accent-primary-glow)'),
  @('rgba(56, 189, 248, 0.55)', 'var(--accent-primary-glow)'),
  @('rgba(56, 189, 248, 0.75)', 'var(--accent-primary-glow)'),
  @('rgba(56, 189, 248, 0.95)', 'var(--accent-primary)'),
  @('rgba(56, 189, 248, 0.02)', 'var(--overlay-1)'),
  @('rgba(251, 146, 60, 0.1)', 'var(--accent-warn-glow)'),
  @('rgba(255, 255, 255, 0.02)', 'var(--overlay-1)'),
  @('rgba(255, 255, 255, 0.025)', 'var(--overlay-1)'),
  @('rgba(255, 255, 255, 0.03)', 'var(--overlay-1)'),
  @('rgba(255, 255, 255, 0.04)', 'var(--overlay-2)'),
  @('rgba(255, 255, 255, 0.05)', 'var(--overlay-2)'),
  @('rgba(255, 255, 255, 0.06)', 'var(--overlay-3)'),
  @('rgba(255, 255, 255, 0.07)', 'var(--overlay-3)'),
  @('rgba(255, 255, 255, 0.08)', 'var(--overlay-3)'),
  @('rgba(255, 255, 255, 0.09)', 'var(--overlay-3)'),
  @('rgba(255, 255, 255, 0.1)', 'var(--overlay-4)'),
  @('rgba(255, 255, 255, 0.12)', 'var(--overlay-4)'),
  @('rgba(255, 255, 255, 0.14)', 'var(--overlay-4)'),
  @('rgba(255, 255, 255, 0.15)', 'var(--overlay-5)'),
  @('rgba(255, 255, 255, 0.18)', 'var(--overlay-5)'),
  @('rgba(255, 255, 255, 0.2)', 'var(--overlay-5)'),
  @('rgba(255, 255, 255, 0.22)', 'var(--overlay-5)'),
  @('rgba(0, 0, 0, 0.65)', 'var(--overlay-5)'),
  @('rgba(0, 0, 0, 0.5)', 'var(--overlay-5)'),
  @('$bg-panel: var(--bg-secondary);', '$bg-panel: var(--bg-surface);'),
  @('$border: var(--overlay-3);', '$border: var(--glass-border);')
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
Write-Host "Pass2 done. $count files."
