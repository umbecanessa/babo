<#
.SYNOPSIS
  Build, package, and publish a new Babo Desktop release.

.DESCRIPTION
  Automates the full release pipeline:
    1. Bumps the version in package.json (patch/minor/major)
    2. Builds Angular frontend (electron config)
    3. Compiles Electron TypeScript
    4. Packages the NSIS installer via electron-builder
    5. Commits the version bump to git
    6. Creates a GitHub Release with all artifacts
    7. Marks the new release as Latest

.PARAMETER Bump
  Version bump type: patch (default), minor, or major.

.PARAMETER SkipGit
  If set, skips the git commit + push step.

.PARAMETER Version
  If set, use this exact version (e.g. 0.4.0) instead of bumping. Use for releasing a specific version.

.EXAMPLE
  .\release.ps1              # patch bump: 0.2.0 -> 0.2.1
  .\release.ps1 -Bump minor  # minor bump: 0.2.1 -> 0.3.0
  .\release.ps1 -Version 0.4.0  # release as 0.4.0 (no bump)
#>

param(
    [ValidateSet("patch", "minor", "major")]
    [string]$Bump = "patch",

    [switch]$SkipGit,

    [string]$Version
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# ── Helpers ───────────────────────────────────────────────────────────────

function Write-Step($msg) { Write-Host "`n>> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "   $msg" -ForegroundColor Green }
function Write-Err($msg)  { Write-Host "   $msg" -ForegroundColor Red; exit 1 }

function Get-PackageVersion {
    $pkg = Get-Content "package.json" -Raw | ConvertFrom-Json
    return $pkg.version
}

function Set-PackageVersion([string]$ver) {
    $raw = Get-Content "package.json" -Raw
    $raw = $raw -replace '"version":\s*"[^"]*"', "`"version`": `"$ver`""
    Set-Content "package.json" $raw -NoNewline
}

function Bump-Version([string]$current, [string]$type) {
    $parts = $current.Split(".")
    $ma = [int]$parts[0]; $mi = [int]$parts[1]; $pa = [int]$parts[2]
    switch ($type) {
        "major" { $ma++; $mi = 0; $pa = 0 }
        "minor" { $mi++; $pa = 0 }
        "patch" { $pa++ }
    }
    return "$ma.$mi.$pa"
}

# ── Pre-flight checks ────────────────────────────────────────────────────

Write-Step "Pre-flight checks"

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) { Write-Err "gh CLI not found. Install: https://cli.github.com" }
if (-not (Get-Command npx -ErrorAction SilentlyContinue)) { Write-Err "npx not found. Install Node.js" }

$oldVersion = Get-PackageVersion
if ($Version) {
    $newVersion = $Version
    Write-Ok "Current version : $oldVersion"
    Write-Ok "Target version  : $newVersion (explicit)"
} else {
    $newVersion = Bump-Version $oldVersion $Bump
    Write-Ok "Current version : $oldVersion"
    Write-Ok "New version     : $newVersion ($Bump bump)"
}
$tag = "v$newVersion"
Write-Ok "Git tag         : $tag"

# ── 1. Set version ───────────────────────────────────────────────────────

Write-Step "Setting version to $newVersion"
Set-PackageVersion $newVersion
Write-Ok "package.json updated"

# ── 2. Build Angular frontend ────────────────────────────────────────────

Write-Step "Building Angular frontend (electron config)"
Push-Location "..\frontend"
npx ng build --configuration=electron
if ($LASTEXITCODE -ne 0) { Pop-Location; Write-Err "Angular build failed" }
Pop-Location
Write-Ok "Angular build complete"

# ── 3. Compile Electron TypeScript ────────────────────────────────────────

Write-Step "Compiling Electron TypeScript"
npm run build:electron
if ($LASTEXITCODE -ne 0) { Write-Err "Electron TS compilation failed" }
Write-Ok "Electron compiled"

# ── 4. Package installer ─────────────────────────────────────────────────

Write-Step "Packaging NSIS installer"
if (Test-Path "release") { Remove-Item -Recurse -Force "release" }
npx electron-builder --win nsis --publish never
if ($LASTEXITCODE -ne 0) { Write-Err "electron-builder failed" }

$installer = Get-ChildItem "release\*.exe" | Where-Object { $_.Name -notlike "*uninstaller*" -and $_.Name -like "*Setup*" } | Select-Object -First 1
$blockmap  = Get-ChildItem "release\*.blockmap" | Select-Object -First 1
$latestYml = "release\latest.yml"

if (-not $installer) { Write-Err "Installer .exe not found in release\" }
if (-not $blockmap)  { Write-Err "Blockmap not found in release\" }
if (-not (Test-Path $latestYml)) { Write-Err "latest.yml not found in release\" }

Write-Ok "Installer : $($installer.Name)"
Write-Ok "Blockmap  : $($blockmap.Name)"

# ── 5. Fix latest.yml filename if needed ──────────────────────────────────
#    electron-builder may produce hyphens in latest.yml but dots in the
#    actual filename (or vice-versa). Ensure they match.

$ymlContent = Get-Content $latestYml -Raw
$actualName = $installer.Name
if ($ymlContent -notmatch [regex]::Escape($actualName)) {
    Write-Step "Fixing latest.yml to match actual installer filename"
    $ymlContent = $ymlContent -replace 'Babo[-. ]Setup[-. ]\d+\.\d+\.\d+\.exe', $actualName
    Set-Content $latestYml $ymlContent -NoNewline
    Write-Ok "latest.yml patched to reference $actualName"
}

# ── 6. Git commit + push ─────────────────────────────────────────────────

if (-not $SkipGit) {
    Write-Step "Committing version bump to git"
    Push-Location ".."
    git add desktop/package.json
    git commit -m "release: $tag"
    if ($LASTEXITCODE -ne 0) { Pop-Location; Write-Err "Git commit failed" }
    git push
    if ($LASTEXITCODE -ne 0) { Pop-Location; Write-Err "Git push failed" }
    Pop-Location
    Write-Ok "Committed and pushed"
} else {
    Write-Ok "Skipping git (--SkipGit)"
}

# ── 7. Create GitHub Release ─────────────────────────────────────────────

Write-Step "Creating GitHub Release $tag"

$existingRelease = gh release view $tag -R umbecanessa/babo 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "   Release $tag already exists — deleting it first" -ForegroundColor Yellow
    gh release delete $tag -R umbecanessa/babo --yes
}

gh release create $tag `
    -R umbecanessa/babo `
    --title $tag `
    --notes "Release $tag" `
    --latest `
    $installer.FullName `
    $blockmap.FullName `
    $latestYml

if ($LASTEXITCODE -ne 0) { Write-Err "GitHub release creation failed" }

Write-Ok "Release published: https://github.com/umbecanessa/babo/releases/tag/$tag"

# ── Done ──────────────────────────────────────────────────────────────────

Write-Host "`n✅ Release $tag complete!`n" -ForegroundColor Green
Write-Host "Users running older versions will see the update notification automatically." -ForegroundColor DarkGray
