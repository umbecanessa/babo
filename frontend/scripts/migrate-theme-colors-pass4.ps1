$root = Join-Path $PSScriptRoot '..\src\app'
$files = Get-ChildItem -Path $root -Recurse -Include *.scss

$replacements = @(
  @('#6b7280', 'var(--text-muted)'),
  @('#4b5563', 'var(--text-muted)'),
  @('#374151', 'var(--text-muted)'),
  @('#1f2937', 'var(--bg-secondary)'),
  @('#f3f4f6', 'var(--text-primary)'),
  @('#e5e7eb', 'var(--text-secondary)'),
  @('#d1d5db', 'var(--text-secondary)'),
  @('#93c5fd', 'var(--accent-primary)'),
  @('#60a5fa', 'var(--accent-primary)'),
  @('#a5b4fc', 'var(--accent-primary)'),
  @('#f472b6', 'var(--accent-primary)'),
  @('#22c55e', 'var(--accent-success)'),
  @('#16a34a', 'var(--accent-success)'),
  @('#eab308', 'var(--accent-warn)'),
  @('#dc2626', 'var(--accent-danger)'),
  @('#b91c1c', 'var(--accent-danger)'),
  @('color-scheme: dark', 'color-scheme: light dark')
)

$count = 0
foreach ($file in $files) {
  $c = [IO.File]::ReadAllText($file.FullName)
  $n = $c
  foreach ($pair in $replacements) { $n = $n.Replace($pair[0], $pair[1]) }
  if ($n -ne $c) { [IO.File]::WriteAllText($file.FullName, $n); $count++; Write-Host $file.Name }
}
Write-Host "Pass4: $count"
