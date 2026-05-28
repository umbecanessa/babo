$root = Join-Path $PSScriptRoot '..\src\app'
$files = Get-ChildItem -Path $root -Recurse -Include *.scss

$replacements = @(
  @('#0a0a0e', 'var(--bg-secondary)'),
  @('#08080c', 'var(--bg-primary)'),
  @('#333', 'var(--overlay-4)'),
  @('#666', 'var(--text-muted)'),
  @('#555', 'var(--text-muted)'),
  @('#c0c8e0', 'var(--text-secondary)'),
  @('#a8b4d0', 'var(--text-secondary)'),
  @('#e0e8f8', 'var(--text-primary)'),
  @('#8090b0', 'var(--text-muted)'),
  @('#8898b8', 'var(--text-muted)'),
  @('#a0a8c0', 'var(--text-muted)'),
  @('#c0c0d4', 'var(--text-secondary)'),
  @('#a0a0b8', 'var(--text-muted)'),
  @('#e0e0e0', 'var(--text-primary)'),
  @('#c7d2fe', 'var(--accent-primary)'),
  @('#7dd3fc', 'var(--accent-secondary)'),
  @('#fcd34d', 'var(--accent-warn)'),
  @('#c0c0d0', 'var(--text-secondary)'),
  @('#c0c0d8', 'var(--text-secondary)'),
  @('#a0a0b0', 'var(--text-muted)'),
  @('#6b6b80', 'var(--text-muted)'),
  @('#8a8a98', 'var(--text-muted)'),
  @('#8888a0', 'var(--text-muted)'),
  @('#c4b5fd', 'var(--accent-primary)'),
  @('#5eead4', 'var(--accent-secondary)'),
  @('#3b82f6', 'var(--accent-primary)'),
  @('rgba(59, 130, 246, 0.12)', 'var(--accent-primary-glow)'),
  @('background: #0088cc', 'background: #0088cc'),
  @('color: #0088cc', 'color: #0088cc')
)

$count = 0
foreach ($file in $files) {
  $c = [IO.File]::ReadAllText($file.FullName)
  $n = $c
  foreach ($pair in $replacements) { $n = $n.Replace($pair[0], $pair[1]) }
  if ($n -ne $c) { [IO.File]::WriteAllText($file.FullName, $n); $count++; Write-Host $file.Name }
}
Write-Host "Pass5: $count"
