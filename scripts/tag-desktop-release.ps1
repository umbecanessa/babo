<#
.SYNOPSIS
  Bump desktop version, commit, tag, and push — triggers GitHub Actions Release Desktop.

.DESCRIPTION
  Does NOT build installers locally. After push, see:
    https://github.com/umbecanessa/babo/actions/workflows/release-desktop.yml

.PARAMETER Bump
  patch (default), minor, or major.

.PARAMETER Version
  Exact semver (e.g. 1.9.7) instead of bumping.

.PARAMETER Branch
  Branch to commit and push (default: main).

.PARAMETER DryRun
  Show planned steps without changing git or remote.

.EXAMPLE
  .\scripts\tag-desktop-release.ps1
  .\scripts\tag-desktop-release.ps1 -Bump minor
  .\scripts\tag-desktop-release.ps1 -Version 1.0.0
  .\scripts\tag-desktop-release.ps1 --version 1.0.0 -DryRun
  .\scripts\tag-desktop-release.ps1 -DryRun
#>

$ErrorActionPreference = "Stop"

# Supports PowerShell (-Version) and bash-style (--version) flags.
$Bump = "patch"
$Version = ""
$Branch = "main"
$DryRun = $false

for ($i = 0; $i -lt $args.Count; $i++) {
    $arg = $args[$i]
    switch -Regex ($arg) {
        '^(-Version|--version|-v)$' {
            if ($i + 1 -ge $args.Count) { throw "Missing value after $arg" }
            $Version = $args[++$i]
            continue
        }
        '^(-Bump|--bump)$' {
            if ($i + 1 -ge $args.Count) { throw "Missing value after $arg" }
            $Bump = $args[++$i]
            continue
        }
        '^--patch$' { $Bump = "patch"; continue }
        '^--minor$' { $Bump = "minor"; continue }
        '^--major$' { $Bump = "major"; continue }
        '^(-DryRun|--dry-run)$' { $DryRun = $true; continue }
        '^(-Branch|--branch)$' {
            if ($i + 1 -ge $args.Count) { throw "Missing value after $arg" }
            $Branch = $args[++$i]
            continue
        }
        '^-Bump$' {
            if ($i + 1 -ge $args.Count) { throw "Missing value after -Bump" }
            $Bump = $args[++$i]
            continue
        }
        default {
            if ($arg -match '^\d+\.\d+\.\d+$') {
                $Version = $arg
                continue
            }
            throw "Unknown argument: $arg (use -Version 1.0.0 or --version 1.0.0)"
        }
    }
}

if ($Bump -notin @("patch", "minor", "major")) {
    throw "Bump must be patch, minor, or major (got: $Bump)"
}
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PkgPath = Join-Path $RepoRoot "desktop\package.json"
Set-Location $RepoRoot

function Write-Step($msg) { Write-Host "`n>> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "   $msg" -ForegroundColor Green }
function Write-Err($msg)  { Write-Host "   $msg" -ForegroundColor Red; exit 1 }

function Get-PackageVersion {
    $pkg = Get-Content $PkgPath -Raw | ConvertFrom-Json
    return $pkg.version
}

function Set-PackageVersion([string]$ver) {
    $raw = Get-Content $PkgPath -Raw
    $raw = $raw -replace '"version":\s*"[^"]*"', "`"version`": `"$ver`""
    Set-Content $PkgPath $raw -NoNewline
}

function Bump-Version([string]$current, [string]$type) {
    $parts = $current.Split(".")
    if ($parts.Count -ne 3) { Write-Err "Version must be semver X.Y.Z (got: $current)" }
    $ma = [int]$parts[0]; $mi = [int]$parts[1]; $pa = [int]$parts[2]
    switch ($type) {
        "major" { $ma++; $mi = 0; $pa = 0 }
        "minor" { $mi++; $pa = 0 }
        "patch" { $pa++ }
    }
    return "$ma.$mi.$pa"
}

function Invoke-Git([string[]]$GitArgv) {
    if ($DryRun) {
        Write-Host "   [dry-run] git $($GitArgv -join ' ')" -ForegroundColor DarkGray
        return
    }
    & git @GitArgv
    if ($LASTEXITCODE -ne 0) { Write-Err "git $($GitArgv -join ' ') failed" }
}

# ── Pre-flight ─────────────────────────────────────────────────────────────

Write-Step "Pre-flight"

if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Write-Err "git not found" }

$branch = (git rev-parse --abbrev-ref HEAD 2>$null).Trim()
if ($branch -ne $Branch) {
    Write-Err "Current branch is '$branch'. Checkout '$Branch' first (or pass -Branch)."
}

function Get-ReleaseBlockingDirty {
    $ignore = @(
        '^desktop/dist-electron/',
        '^desktop/release/',
        '^desktop/release-build/',
        '^desktop/release-mac/'
    )
    git status --porcelain --untracked-files=no | Where-Object {
        $path = ($_ -replace '^\S+\s+', '').Trim() -replace '\\', '/'
        if ($path -match ' -> ') { $path = ($path -split ' -> ')[-1].Trim() -replace '\\', '/' }
        $blocked = $true
        foreach ($pat in $ignore) {
            if ($path -match $pat) { $blocked = $false; break }
        }
        $blocked
    }
}

$dirty = @(Get-ReleaseBlockingDirty)
if ($dirty.Count -gt 0 -and -not $DryRun) {
    Write-Host "   Uncommitted changes:" -ForegroundColor Yellow
    $dirty | ForEach-Object { Write-Host "     $_" }
    Write-Err "Commit or stash other changes before releasing."
}

$oldVersion = Get-PackageVersion
if ($Version) {
    if ($Version -notmatch '^\d+\.\d+\.\d+$') { Write-Err "Version must be X.Y.Z (got: $Version)" }
    $newVersion = $Version
} else {
    $newVersion = Bump-Version $oldVersion $Bump
}

$tag = "v$newVersion"
Write-Ok "Current version : $oldVersion"
Write-Ok "Release version : $newVersion"
Write-Ok "Tag             : $tag"
Write-Ok "Branch          : $Branch"

if (-not $DryRun) {
    $remoteTag = git ls-remote --tags origin "refs/tags/$tag" 2>$null
    if ($remoteTag) { Write-Err "Tag $tag already exists on origin. Delete it or pick another version." }
    $localTag = git tag -l $tag
    if ($localTag) { Write-Err "Local tag $tag already exists. Delete it: git tag -d $tag" }
}

# ── Version bump + git ─────────────────────────────────────────────────────

Write-Step "Updating desktop/package.json"
if ($DryRun) {
    Write-Host "   [dry-run] set version -> $newVersion" -ForegroundColor DarkGray
} else {
    Set-PackageVersion $newVersion
    Write-Ok "desktop/package.json -> $newVersion"
}

Write-Step "Commit, push branch, tag, push tag"
Invoke-Git @("add", "desktop/package.json")
Invoke-Git @("commit", "-m", "release: $tag")
Invoke-Git @("push", "origin", $Branch)
Invoke-Git @("tag", "-a", $tag, "-m", "release: $tag")
Invoke-Git @("push", "origin", $tag)

# ── Done ───────────────────────────────────────────────────────────────────

Write-Host ""
if ($DryRun) {
    Write-Host "Dry run complete (no changes made)." -ForegroundColor Yellow
} else {
    Write-Host "Tagged and pushed $tag on $Branch." -ForegroundColor Green
    Write-Host "CI will build and publish:" -ForegroundColor DarkGray
    Write-Host "  https://github.com/umbecanessa/babo/actions/workflows/release-desktop.yml" -ForegroundColor DarkGray
    Write-Host "  https://github.com/umbecanessa/babo/releases/tag/$tag" -ForegroundColor DarkGray
}
Write-Host ""
