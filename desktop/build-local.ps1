<#
.SYNOPSIS
  Build Babo Desktop locally (no version bump, no GitHub release).

.DESCRIPTION
  1. Angular build (electron configuration)
  2. Electron TypeScript compile
  3. electron-builder --dir (unpacked app in desktop/release/win-unpacked)

  For a Windows installer (.exe), run:  npm run dist:win
  For full publish pipeline, run:       .\release.ps1 -SkipGit  (then add -SkipGit and manual gh if needed)

.EXAMPLE
  .\build-local.ps1
  .\build-local.ps1 -Installer   # also run NSIS (slower)
#>

param(
    [switch]$Installer
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Write-Step($msg) { Write-Host "`n>> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "   $msg" -ForegroundColor Green }
function Write-Err($msg)  { Write-Host "   $msg" -ForegroundColor Red; exit 1 }

if (-not (Get-Command npx -ErrorAction SilentlyContinue)) { Write-Err "npx not found. Install Node.js 20+." }

function Ensure-BuildIcons {
    $buildDir = Join-Path $PSScriptRoot "build"
    $iconPng = Join-Path $buildDir "icon.png"
    $iconIco = Join-Path $buildDir "icon.ico"
    if ((Test-Path $iconPng) -and (Test-Path $iconIco)) { return }
    New-Item -ItemType Directory -Force -Path $buildDir | Out-Null
    $baboPng = Join-Path $PSScriptRoot "..\frontend\src\assets\images\babo.png"
    $favicon = Join-Path $PSScriptRoot "..\frontend\public\favicon.ico"
    if (-not (Test-Path $baboPng)) { Write-Err "Missing $baboPng — cannot create desktop icons" }
    if (-not (Test-Path $favicon)) { Write-Err "Missing $favicon — cannot create desktop icons" }
    Copy-Item $baboPng $iconPng -Force
    Copy-Item $favicon $iconIco -Force
    Write-Ok "Created build/icon.png and build/icon.ico from frontend assets"
}

Ensure-BuildIcons

Write-Step "Building Angular (electron config)"
Push-Location "..\frontend"
npx ng build --configuration=electron
if ($LASTEXITCODE -ne 0) { Pop-Location; Write-Err "Angular build failed" }
Pop-Location
Write-Ok "Angular OK"

Write-Step "Compiling Electron TypeScript"
npm run build:electron
if ($LASTEXITCODE -ne 0) { Write-Err "Electron compile failed" }
Write-Ok "Electron OK"

if ($Installer) {
    Write-Step "Packaging NSIS installer"
    if (Test-Path "release") { Remove-Item -Recurse -Force "release" -ErrorAction SilentlyContinue }
    npx electron-builder --win nsis --publish never
    if ($LASTEXITCODE -ne 0) { Write-Err "electron-builder failed" }
    $exe = Get-ChildItem "release\*.exe" | Where-Object { $_.Name -like "*Setup*" } | Select-Object -First 1
    if ($exe) { Write-Ok "Installer: $($exe.FullName)" }
} else {
    Write-Step "Packaging unpacked app (--dir)"
    if (Test-Path "release") { Remove-Item -Recurse -Force "release" -ErrorAction SilentlyContinue }
    npx electron-builder --dir --publish never
    if ($LASTEXITCODE -ne 0) { Write-Err "electron-builder failed" }
    Write-Ok "Unpacked app: $PWD\release\win-unpacked\Babo.exe"
}

Write-Host "`nDone. Run the app: .\release\win-unpacked\Babo.exe`n" -ForegroundColor Green
