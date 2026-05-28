$root = Join-Path $PSScriptRoot '..\src\app'
$files = Get-ChildItem -Path $root -Recurse -Include *.scss

$replacements = @(
  @('$slate: #94a3b8;', '$slate: var(--text-muted);'),
  @('#e2e8f0', 'var(--text-primary)'),
  @('#cbd5e1', 'var(--text-secondary)'),
  @('#b0b0c8', 'var(--text-secondary)'),
  @('#8b8ba0', 'var(--text-muted)'),
  @('#a8a8c0', 'var(--text-muted)'),
  @('#7a7a8e', 'var(--text-muted)'),
  @('#e0e7ff', 'var(--text-primary)'),
  @('#c8ccfa', 'var(--accent-primary)'),
  @('#93bbfc', 'var(--accent-primary)'),
  @('#e0e0ec', 'var(--text-primary)'),
  @('#7eb8da', 'var(--accent-secondary)'),
  @('#fde68a', 'var(--accent-warn)'),
  @('#0f172a', 'white'),
  @('#5dcdfb', 'var(--accent-secondary)'),
  @('#4ade80', 'var(--accent-success)'),
  @('#8b5cf6', 'var(--accent-primary)'),
  @('#7c3aed', 'var(--accent-primary)'),
  @('#525252', 'var(--text-muted)'),
  @('background: #fff', 'background: var(--text-primary)'),
  @('rgba(52, 211, 153, 0.3)', 'var(--accent-success-glow)')
)

$count = 0
foreach ($file in $files) {
  $c = [IO.File]::ReadAllText($file.FullName)
  $n = $c
  foreach ($pair in $replacements) { $n = $n.Replace($pair[0], $pair[1]) }
  if ($n -ne $c) { [IO.File]::WriteAllText($file.FullName, $n); $count++; Write-Host $file.Name }
}
Write-Host "Pass6: $count"
