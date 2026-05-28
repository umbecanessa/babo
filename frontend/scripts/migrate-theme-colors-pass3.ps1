$root = Join-Path $PSScriptRoot '..\src\app'
$files = Get-ChildItem -Path $root -Recurse -Include *.scss

$replacements = @(
  @('$text-primary: #c5c5d0;', '$text-primary: var(--text-primary);'),
  @('$text-secondary: #a0a0b0;', '$text-secondary: var(--text-secondary);'),
  @('$text-dim: #4a4a5a;', '$text-dim: var(--text-muted);'),
  @('#9a9aaa', 'var(--text-muted)'),
  @('#9a8bc8', 'var(--accent-primary)'),
  @('#8888aa', 'var(--text-muted)'),
  @('#a8a8b8', 'var(--text-secondary)'),
  @('#b0b0d0', 'var(--text-secondary)'),
  @('#b0b8c8', 'var(--text-secondary)'),
  @('#bac0d0', 'var(--text-secondary)'),
  @('#c8c8d8', 'var(--text-secondary)'),
  @('#d0d0d0', 'var(--text-primary)'),
  @('#d0d0e0', 'var(--text-primary)'),
  @('#d0d0e8', 'var(--text-primary)'),
  @('#e0e0e8', 'var(--text-primary)'),
  @('#e8e8e8', 'var(--text-primary)'),
  @('#e8e8f0', 'var(--text-primary)'),
  @('#e0c8f0', 'var(--accent-primary)'),
  @('#d0e8f8', 'var(--accent-secondary)'),
  @('#b0d4f0', 'var(--text-secondary)'),
  @('#e0f0ff', 'var(--text-primary)'),
  @('#c0d0e0', 'var(--text-secondary)'),
  @('#808090', 'var(--text-muted)'),
  @('#6a6a7a', 'var(--text-muted)'),
  @('#6a8a9a', 'var(--text-muted)'),
  @('#5a5a6a', 'var(--text-muted)'),
  @('#4a4a5a', 'var(--text-muted)'),
  @('#7a7a8a', 'var(--text-muted)'),
  @('#fca5a5', 'var(--accent-danger)'),
  @('#c084fc', 'var(--accent-primary)'),
  @('#2dd4bf', 'var(--accent-secondary)'),
  @('#64748b', 'var(--text-muted)'),
  @('color: #fff', 'color: white'),
  @('rgba(167, 139, 250', 'var(--accent-primary-glow'),
  @('rgba(248, 81, 73', 'rgba(192, 57, 43')
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
    Write-Host $file.Name
  }
}
Write-Host "Pass3: $count files"
