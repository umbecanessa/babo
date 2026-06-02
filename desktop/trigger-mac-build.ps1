<#
.SYNOPSIS
  Trigger a macOS build on the Mac Mini and download the DMG(s) to release-mac/.
  Use this to verify SSH + build + SCP works before wiring into release-all.ps1.

.EXAMPLE
  .\trigger-mac-build.ps1
#>

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$MacHost = "umbertocanessa@192.168.68.76"
$MacRepoPath = "~/Documents/GitHub/babo"

# Build script to run on the Mac (LF only, no CRLF). Source profile so npm is on PATH.
$macScript = @"
set -e
source ~/.zshrc 2>/dev/null || true
source ~/.nvm/nvm.sh 2>/dev/null || true
cd $MacRepoPath/desktop
xattr -cr node_modules 2>/dev/null || true
chmod -R +x node_modules 2>/dev/null || true
export CSC_IDENTITY_AUTO_DISCOVERY=false
echo '>> Running npm run dist:mac...'
npm run dist:mac
echo '>> Creating tarball...'
cd release
tar czf /tmp/babo-release-mac.tar.gz *
echo '>> Done on Mac.'
"@

# Use LF only so remote bash does not see \r
$macScript = $macScript -replace "`r`n", "`n" -replace "`r", "`n"

Write-Host ">> 1. SSH: trigger build on Mac" -ForegroundColor Cyan
$macScript | ssh -o BatchMode=yes -o ConnectTimeout=10 $MacHost "zsh -l -c 'bash -s'"
if ($LASTEXITCODE -ne 0) { Write-Host "   Mac build failed" -ForegroundColor Red; exit 1 }
Write-Host "   Mac build OK" -ForegroundColor Green

Write-Host "`n>> 2. SCP: download tarball" -ForegroundColor Cyan
$releaseMacDir = "release-mac"
if (Test-Path $releaseMacDir) { Remove-Item -Recurse -Force $releaseMacDir }
New-Item -ItemType Directory -Path $releaseMacDir | Out-Null

scp -o BatchMode=yes "${MacHost}:/tmp/babo-release-mac.tar.gz" "$releaseMacDir/mac.tar.gz"
if ($LASTEXITCODE -ne 0) { Write-Host "   SCP failed" -ForegroundColor Red; exit 1 }
Write-Host "   Download OK" -ForegroundColor Green

Write-Host "`n>> 3. Extract" -ForegroundColor Cyan
tar -xzf "$releaseMacDir/mac.tar.gz" -C $releaseMacDir
Remove-Item "$releaseMacDir/mac.tar.gz" -Force
Get-ChildItem $releaseMacDir | ForEach-Object { Write-Host "   $($_.Name)" }
Write-Host "`nDone. Mac artifacts are in desktop/$releaseMacDir/" -ForegroundColor Green
