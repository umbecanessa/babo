$root = Join-Path $PSScriptRoot '..\src'
$files = Get-ChildItem -Path $root -Recurse -Include *.scss,*.ts
$count = 0
foreach ($file in $files) {
  $c = [System.IO.File]::ReadAllText($file.FullName)
  $n = [regex]::Replace($c, 'var\(--accent-primary-glow,\s*[0-9.]+\)', 'var(--accent-primary-glow)')
  $n = [regex]::Replace($n, 'var\(--accent-secondary-glow,\s*[0-9.]+\)', 'var(--accent-secondary-glow)')
  $n = [regex]::Replace($n, 'var\(--accent-warn-glow,\s*[0-9.]+\)', 'var(--accent-warn-glow)')
  if ($n -ne $c) {
    [System.IO.File]::WriteAllText($file.FullName, $n)
    $count++
    Write-Host $file.Name
  }
}
Write-Host "Fixed $count files"
