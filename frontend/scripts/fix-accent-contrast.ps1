# Fix purple-on-purple: accent text on accent-tint backgrounds, glow used as text color.
$root = Join-Path $PSScriptRoot '..\src\app'

$files = Get-ChildItem -Path $root -Recurse -Include *.scss,*.ts

$changed = 0
foreach ($file in $files) {
  $c = [IO.File]::ReadAllText($file.FullName)
  $n = $c

  # Glow token misused as text color (nearly invisible on tint bg)
  $n = [regex]::Replace($n, 'color:\s*var\(--accent-primary-glow\)', 'color: var(--text-muted)')

  # Tint surface + accent label -> readable body text
  $n = [regex]::Replace(
    $n,
    '(background:\s*var\(--accent-primary-glow\);\s*(?:[^\n]*\n\s*){0,8}?)color:\s*var\(--accent-primary\)',
    '${1}color: var(--text-primary)'
  )

  $n = [regex]::Replace(
    $n,
    '(background:\s*var\(--accent-primary-glow\)[^;]*;\s*border:[^;]*;\s*)color:\s*var\(--accent-primary\)',
    '${1}color: var(--text-primary)'
  )

  if ($n -ne $c) {
    [IO.File]::WriteAllText($file.FullName, $n)
    $changed++
    Write-Host $file.Name
  }
}
Write-Host "Fixed $changed files"
