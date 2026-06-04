<#
.SYNOPSIS
  Build Babo Desktop locally (no version bump, no GitHub release).

.DESCRIPTION
  1. Angular build (electron configuration)
  2. Electron TypeScript compile
  3. electron-builder --dir (unpacked app in desktop/release-build/win-unpacked)

  For a Windows installer (.exe), run:  npm run dist:win
  For full publish pipeline, run:       .\release.ps1 -SkipGit  (then add -SkipGit and manual gh if needed)

.EXAMPLE
  .\build-local.ps1
  .\build-local.ps1 -KeepRunning   # do not close Babo.exe (build uses a fresh output folder)
  .\build-local.ps1 -Installer   # also run NSIS (slower)
#>

param(
    [switch]$Installer,
    [switch]$KeepRunning
)

$script:KeepRunning = [bool]$KeepRunning

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
    if (-not (Test-Path $baboPng)) { Write-Err "Missing $baboPng - cannot create desktop icons" }
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
        Write-Err "python required to generate build/icon.ico (pip install pillow)"
    }
    $makeIcon = Join-Path $buildDir "make-icon.py"
    python $makeIcon $baboPng
    if ($LASTEXITCODE -ne 0) { Write-Err "Failed to generate icons - pip install pillow" }
    Write-Ok "Created build/icon.png and build/icon.ico (256x256) from babo.png"
}

Ensure-BuildIcons

function Invoke-GenesisRegenerate {
    Write-Step "Regenerating genesis template from nls/config"
    Push-Location (Join-Path $PSScriptRoot "..")
    python scripts/regenerate-genesis.py
    if ($LASTEXITCODE -ne 0) { Pop-Location; Write-Err "Genesis regeneration failed" }
    Pop-Location
    Write-Ok "genesis_templates/standard-v1 synced"
}

Invoke-GenesisRegenerate

function Stop-BaboLocks {
    if ($script:KeepRunning) {
        Write-Host "   KeepRunning: leaving Babo.exe and Electron processes untouched." -ForegroundColor DarkGray
        return
    }
    foreach ($name in @("Babo", "electron")) {
        Get-Process -Name $name -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    }

    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $p = $_.ExecutablePath
            $p -and (
                $p -like "*\babo\desktop\release-build\*" -or
                $p -like "*\babo\desktop\release\win-unpacked\*"
            )
        } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

    Start-Sleep -Seconds 2
}

function Clear-ReadOnlyTree([string]$fullPath) {
    if (-not (Test-Path -LiteralPath $fullPath)) { return }
    try {
        $root = Get-Item -LiteralPath $fullPath -Force
        $root.Attributes = $root.Attributes -band (-bnot [System.IO.FileAttributes]::ReadOnly)
    } catch { }
    Get-ChildItem -LiteralPath $fullPath -Recurse -Force -ErrorAction SilentlyContinue | ForEach-Object {
        try {
            $_.Attributes = $_.Attributes -band (-bnot [System.IO.FileAttributes]::ReadOnly)
        } catch { }
    }
}

function Remove-ArtifactDir([string]$path) {
    if (-not (Test-Path -LiteralPath $path)) { return $true }
    Clear-ReadOnlyTree $path
    try {
        Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction Stop
        return $true
    } catch {
        Write-Host "   Could not remove $path : $($_.Exception.Message)" -ForegroundColor Yellow
        return $false
    }
}

function Clear-PreviousArtifacts {
    Write-Step "Cleaning previous build artifacts"
    if (-not $script:KeepRunning) {
        Stop-BaboLocks
    }

    $releaseBuild = Join-Path $PSScriptRoot "release-build"
    if (Test-Path $releaseBuild) {
        Get-ChildItem $releaseBuild -Directory -ErrorAction SilentlyContinue | ForEach-Object {
            $name = $_.Name
            if ($name -like "build-*" -or $name -like "win-unpacked.bak.*") {
                if (Remove-ArtifactDir $_.FullName) {
                    Write-Ok "Removed release-build\$name"
                }
            }
        }
    }

    if ($Installer) {
        $release = Join-Path $PSScriptRoot "release"
        if (Test-Path $release) {
            if (Remove-ArtifactDir $release) {
                Write-Ok "Removed release\"
            }
        }
    }
}

function Test-BaboExe($UnpackedDir) {
    $exe = Join-Path $UnpackedDir "Babo.exe"
    return (Test-Path -LiteralPath $exe -PathType Leaf)
}

function Copy-UnpackedTree {
    param(
        [string]$Source,
        [string]$Dest
    )
    New-Item -ItemType Directory -Force -Path $Dest | Out-Null
    # robocopy: exit 0-7 = success (incl. files copied)
    $null = & robocopy $Source $Dest /E /MIR /R:2 /W:2 /NFL /NDL /NJH /NJS /NC /NS
    if ($LASTEXITCODE -ge 8) {
        throw "robocopy failed with exit code $LASTEXITCODE"
    }
    if (-not (Test-BaboExe $Dest)) {
        throw "Babo.exe missing after copy to $Dest"
    }
}

function Promote-UnpackedBuild {
    param(
        [string]$BuiltUnpacked,
        [string]$StableUnpacked
    )

    if (-not (Test-Path $BuiltUnpacked)) {
        Write-Err "Build output missing: $BuiltUnpacked"
    }
    if (-not (Test-BaboExe $BuiltUnpacked)) {
        Write-Err "Build incomplete - Babo.exe missing in $BuiltUnpacked"
    }

    Stop-BaboLocks

    $parent = Split-Path $StableUnpacked -Parent
    if (Test-Path $StableUnpacked) {
        $stamp = Get-Date -Format "yyyyMMddHHmmss"
        $bakName = "win-unpacked.bak.$stamp"
        $bak = Join-Path $parent $bakName
        try {
            Rename-Item -Path $StableUnpacked -NewName $bakName -Force -ErrorAction Stop
            Write-Ok "Moved previous build aside -> $bak"
        } catch {
            Write-Host "   Previous win-unpacked is locked (close Babo.exe and Explorer on that folder)." -ForegroundColor Yellow
            if (Test-BaboExe $BuiltUnpacked) {
                Write-Host "   Using fresh build at: $BuiltUnpacked" -ForegroundColor Green
                return $BuiltUnpacked
            }
            Write-Err "Cannot promote and Babo.exe not found in build output"
        }
    }

    $maxTries = 4
    for ($i = 1; $i -le $maxTries; $i++) {
        try {
            Copy-UnpackedTree -Source $BuiltUnpacked -Dest $StableUnpacked
            Write-Ok "Updated $StableUnpacked"
            try {
                Remove-Item -Path $BuiltUnpacked -Recurse -Force -ErrorAction SilentlyContinue
            } catch {
                Write-Host "   (Left build cache at $BuiltUnpacked - could not delete after copy)" -ForegroundColor DarkGray
            }
            return $StableUnpacked
        } catch {
            if ($i -lt $maxTries) {
                Write-Host "   Promote attempt $i/$maxTries failed: $($_.Exception.Message)" -ForegroundColor Yellow
                Stop-BaboLocks
                Start-Sleep -Seconds 2
            }
        }
    }

    if (Test-BaboExe $StableUnpacked) {
        Write-Host "   Promote copy failed, but $StableUnpacked already has Babo.exe - using it." -ForegroundColor Yellow
        return $StableUnpacked
    }
    if (Test-BaboExe $BuiltUnpacked) {
        Write-Host "   Promote failed; run the build from:" -ForegroundColor Yellow
        Write-Host "   $(Join-Path $BuiltUnpacked 'Babo.exe')" -ForegroundColor Green
        return $BuiltUnpacked
    }

    Write-Err "Promote failed and Babo.exe not found in build output or win-unpacked"
}

Clear-PreviousArtifacts

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
    Stop-BaboLocks
    npx electron-builder --win nsis --publish never
    if ($LASTEXITCODE -ne 0) { Write-Err "electron-builder failed" }
    $exe = Get-ChildItem "release\*.exe" | Where-Object { $_.Name -like "*Setup*" } | Select-Object -First 1
    if ($exe) { Write-Ok "Installer: $($exe.FullName)" }
} else {
    Write-Step "Packaging unpacked app (--dir)"
    # Timestamped output dir — no need to kill a running Babo unless promoting into win-unpacked.

    # Fresh output dir avoids electron-builder failing on a locked app.asar in win-unpacked
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $outDir = Join-Path $PSScriptRoot "release-build\build-$stamp"
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null

    npx electron-builder --dir --publish never --config.directories.output=$outDir
    if ($LASTEXITCODE -ne 0) { Write-Err "electron-builder failed" }

    $builtUnpacked = Join-Path $outDir "win-unpacked"
    $stableUnpacked = Join-Path $PSScriptRoot "release-build\win-unpacked"
    $finalUnpacked = Promote-UnpackedBuild -BuiltUnpacked $builtUnpacked -StableUnpacked $stableUnpacked

    $parent = Split-Path $outDir -Parent
    if ((Get-ChildItem $outDir -ErrorAction SilentlyContinue | Measure-Object).Count -eq 0) {
        Remove-Item $outDir -Force -ErrorAction SilentlyContinue
    }

    $finalExe = Join-Path $finalUnpacked "Babo.exe"
    if (-not (Test-Path -LiteralPath $finalExe -PathType Leaf)) {
        Write-Err "Babo.exe not found at $finalExe"
    }
    Write-Ok "Unpacked app: $finalExe"
    $script:FinalBaboExe = $finalExe
}

$runHint = if ($script:FinalBaboExe) { $script:FinalBaboExe } else { ".\release-build\win-unpacked\Babo.exe" }
Write-Host "`nDone. Run: $runHint`n" -ForegroundColor Green
