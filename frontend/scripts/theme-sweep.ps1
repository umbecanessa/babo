# Full theme sweep: dark panel leftovers -> CSS variables (light/dark glass).
$root = Join-Path $PSScriptRoot '..\src\app'

$files = Get-ChildItem -Path $root -Recurse -Include *.scss,*.ts |
  Where-Object { $_.FullName -notmatch 'theme-colors\.ts$' }

$literalReplacements = @(
  @('background: rgba(5, 5, 8, 0.8)', 'background: var(--glass-bg)'),
  @('background: rgba(5, 5, 10, 0.6)', 'background: var(--glass-bg)'),
  @('background: rgba(8, 8, 12, 0.9)', 'background: var(--glass-bg)'),
  @('background: rgba(10, 11, 14, 0.72)', 'background: var(--glass-bg)'),
  @('background: rgba(12, 12, 16, 0.98)', 'background: var(--glass-bg)'),
  @('background: rgba(20, 20, 28, 0.9)', 'background: var(--glass-bg)'),
  @('background: rgba(0, 0, 0, 0.6)', 'background: var(--backdrop-scrim)'),
  @('background: rgba(0, 0, 0, 0.42)', 'background: var(--backdrop-scrim)'),
  @('background: rgba(0, 0, 0, 0.4)', 'background: var(--backdrop-scrim)'),
  @('background: rgba(0, 0, 0, 0.3)', 'background: var(--surface-inset-strong)'),
  @('background: rgba(0, 0, 0, 0.25)', 'background: var(--surface-inset-strong)'),
  @('background: rgba(0, 0, 0, 0.2)', 'background: var(--surface-inset-strong)'),
  @('background: rgba(0, 0, 0, 0.15)', 'background: var(--surface-inset)'),
  @('background: rgba(0,0,0,0.3)', 'background: var(--surface-inset-strong)'),
  @('background: rgba(0,0,0,0.2)', 'background: var(--surface-inset-strong)'),
  @('background: rgba(0,0,0,0.15)', 'background: var(--surface-inset)'),
  @('color: rgba(230, 230, 238, 0.6)', 'color: var(--text-muted)'),
  @('color: rgba(235, 235, 242, 0.65)', 'color: var(--text-secondary)'),
  @('color: rgba(200, 200, 210, 0.88)', 'color: var(--text-secondary)'),
  @('color: rgba(220, 225, 235, 0.88)', 'color: var(--text-secondary)'),
  @('color: rgba(196, 181, 253, 0.85)', 'color: var(--accent-primary)'),
  @('border-color: rgba(129, 140, 248, 0.3)', 'border-color: var(--accent-primary-glow)'),
  @('border-color: rgba(129, 140, 248, 0.2)', 'border-color: var(--accent-primary-glow)'),
  @('background: rgba(129, 140, 248, 0.15)', 'background: var(--accent-primary-glow)'),
  @('background: rgba(129, 140, 248, 0.25)', 'background: var(--accent-primary-glow)'),
  @('background: rgba(129, 140, 248, 0.08)', 'background: var(--accent-primary-glow)'),
  @('background: rgba(129, 140, 248, 0.06)', 'background: var(--accent-primary-glow)'),
  @('background: rgba(129, 140, 248, 0.04)', 'background: var(--accent-primary-glow)'),
  @('background: rgba(129, 140, 248, 0.1)', 'background: var(--accent-primary-glow)'),
  @('background: rgba(99, 102, 241, 0.15)', 'background: var(--accent-primary-glow)'),
  @('background: rgba(99, 102, 241, 0.12)', 'background: var(--accent-primary-glow)'),
  @('background: rgba(99, 102, 241, 0.1)', 'background: var(--accent-primary-glow)'),
  @('background: rgba(99, 102, 241, 0.06)', 'background: var(--accent-primary-glow)'),
  @('background: rgba(99, 102, 241, 0.04)', 'background: var(--accent-primary-glow)'),
  @('background: rgba(99, 102, 241, 0.08)', 'background: var(--accent-primary-glow)'),
  @('border: 1px solid rgba(129, 140, 248, 0.15)', 'border: 1px solid var(--accent-primary-glow)'),
  @('border: 1px solid rgba(129, 140, 248, 0.12)', 'border: 1px solid var(--accent-primary-glow)'),
  @('border-bottom: 1px solid rgba(99, 102, 241, 0.08)', 'border-bottom: 1px solid var(--accent-primary-glow)'),
  @('border-bottom: 1px solid rgba(99, 102, 241, 0.2)', 'border-bottom: 1px solid var(--accent-primary-glow)'),
  @('border-bottom-color: rgba(99, 102, 241, 0.2)', 'border-bottom-color: var(--accent-primary-glow)'),
  @('$bg-card: var(--overlay-1)', '$bg-card: var(--glass-bg)')
)

$regexReplacements = @(
  @{ Pattern = 'color:\s*rgba\(165,\s*180,\s*252,\s*[0-9.]+\)'; Replacement = 'color: var(--accent-primary)' }
  @{ Pattern = 'color:\s*rgba\(59,\s*130,\s*246,\s*[0-9.]+\)'; Replacement = 'color: var(--accent-primary)' }
)

$changed = 0
foreach ($file in $files) {
  $c = [IO.File]::ReadAllText($file.FullName)
  $n = $c
  foreach ($pair in $literalReplacements) {
    $n = $n.Replace($pair[0], $pair[1])
  }
  foreach ($rx in $regexReplacements) {
    $n = [regex]::Replace($n, $rx.Pattern, $rx.Replacement)
  }
  if ($n -ne $c) {
    [IO.File]::WriteAllText($file.FullName, $n)
    $changed++
    Write-Host $file.FullName.Replace($root, '')
  }
}
Write-Host "Updated $changed files"
