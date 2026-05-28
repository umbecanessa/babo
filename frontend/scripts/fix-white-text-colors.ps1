# Replace hardcoded white text colors (dark-theme leftovers) with theme tokens.
$root = Join-Path $PSScriptRoot '..\src'

function Map-WhiteTextOpacity($opacity) {
  $v = [double]$opacity
  if ($v -ge 0.75) { return 'var(--text-primary)' }
  if ($v -ge 0.45) { return 'var(--text-secondary)' }
  return 'var(--text-muted)'
}

$files = Get-ChildItem -Path $root -Recurse -Include *.scss,*.ts |
  Where-Object { $_.FullName -notmatch 'styles\.scss$' }

$colorPattern = [regex]'color:\s*rgba\(255,\s*255,\s*255,\s*([0-9.]+)\)'
$count = 0

foreach ($file in $files) {
  $c = [IO.File]::ReadAllText($file.FullName)
  $n = $colorPattern.Replace($c, {
    param($m)
    $token = Map-WhiteTextOpacity $m.Groups[1].Value
    "color: $token"
  })
  # Modal / banner title colors
  $n = $n.Replace('color: #f1f5f9', 'color: var(--text-primary)')
  $n = $n.Replace('color: #e2e8f0', 'color: var(--text-secondary)')
  if ($n -ne $c) {
    [IO.File]::WriteAllText($file.FullName, $n)
    $count++
    Write-Host $file.Name
  }
}
Write-Host "Fixed $count files"
