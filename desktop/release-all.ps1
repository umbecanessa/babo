<#
.SYNOPSIS
  Build Babo Desktop for Windows and macOS, then publish one GitHub release with both.

.DESCRIPTION
  Run from this Windows machine. Does:
    1. Bump version (or use -Version)
    2. Build Angular + Electron TS
    3. Build Windows installer (NSIS) locally
    4. Commit and push so the Mac has the new version
    5. SSH to Mac Mini, pull, build macOS DMG(s)
    6. SCP Mac artifacts back
    7. Create one GitHub Release with Windows + Mac artifacts

  Requires: gh, npx, ssh to umbertocanessa@192.168.68.76 (key-based auth).

.PARAMETER Bump
  Version bump: patch (default), minor, or major.

.PARAMETER Version
  Use this exact version instead of bumping (e.g. -Version 0.4.7).

.PARAMETER SkipGit
  Skip git commit and push (use only if you already pushed the version).

.PARAMETER MacRepoPath
  Path to the Babo repo on the Mac (default: ~/Documents/GitHub/babo)

.EXAMPLE
  .\release-all.ps1
  .\release-all.ps1 -Version 0.4.7
  .\release-all.ps1 -Bump minor -MacRepoPath "~/Documents/GitHub/babo"
#>

param(
    [ValidateSet("patch", "minor", "major")]
    [string]$Bump = "patch",

    [switch]$SkipGit,

    [string]$Version,

    [string]$MacRepoPath = "~/Documents/GitHub/babo",

    [string]$MacHost = "umbertocanessa@192.168.68.76"
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

# electron-builder output dirs are often locked (IDE indexing app.asar, unpacked Babo still running, Defender).
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
    $null = & cmd.exe /c "attrib -R `"$fullPath\*`" /S /D" 2>&1
}

# Returns $true if $RelativePath is gone (deleted or renamed away), $false if it still exists.
function Remove-BuildOutputDir {
    param(
        [Parameter(Mandatory)]
        [string]$RelativePath,
        [string]$Hint = "Quit any Babo/Electron started from release\win-unpacked. If a tab or preview targets release\, close it."
    )
    if (-not (Test-Path (Join-Path $PSScriptRoot $RelativePath))) { return $true }

    $full = Join-Path $PSScriptRoot $RelativePath
    Clear-ReadOnlyTree $full

    $attempts = 10
    for ($a = 1; $a -le $attempts; $a++) {
        try {
            Remove-Item -LiteralPath $full -Recurse -Force -ErrorAction Stop
            return $true
        } catch {
            if ($a -eq $attempts) { break }
            Start-Sleep -Milliseconds (250 * $a)
        }
    }
    $null = & cmd.exe /c "rmdir /s /q `"$full`"" 2>&1
    if (-not (Test-Path $full)) { return $true }

    $staleLeaf = "$RelativePath._stale_" + [DateTime]::UtcNow.ToString("yyyyMMdd_HHmmss")
    try {
        Rename-Item -LiteralPath $full -NewName $staleLeaf -ErrorAction Stop
        Write-Host "   $RelativePath is locked - renamed to $staleLeaf (delete when nothing is using it)." -ForegroundColor Yellow
        return $true
    } catch {
        Write-Host "   Could not remove or rename '$RelativePath': $($_.Exception.Message)" -ForegroundColor Yellow
        Write-Host "   $Hint" -ForegroundColor DarkGray
        return $false
    }
}

# ── Pre-flight ─────────────────────────────────────────────────────────────

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

# Test SSH to Mac
Write-Step "Testing SSH to Mac"
$sshTest = ssh -o BatchMode=yes -o ConnectTimeout=10 $MacHost "echo ok" 2>&1
if ($LASTEXITCODE -ne 0) { Write-Err "Cannot SSH to $MacHost. Set up key-based auth (no passphrase)." }
Write-Ok "SSH to Mac OK"

# ── 1. Set version ────────────────────────────────────────────────────────

Write-Step "Setting version to $newVersion"
Set-PackageVersion $newVersion
Write-Ok "package.json updated"

# ── 2. Build Angular + Electron TS ───────────────────────────────────────────

Write-Step "Building Angular frontend (electron config)"
Push-Location "..\frontend"
npx ng build --configuration=electron
if ($LASTEXITCODE -ne 0) { Pop-Location; Write-Err "Angular build failed" }
Pop-Location
Write-Ok "Angular build complete"

Write-Step "Compiling Electron TypeScript"
npm run build:electron
if ($LASTEXITCODE -ne 0) { Write-Err "Electron TS compilation failed" }
Write-Ok "Electron compiled"

# ── 3. Build Windows installer ─────────────────────────────────────────────

Write-Step "Packaging Windows NSIS installer"
$ReleaseOut = "release"
if (-not (Remove-BuildOutputDir -RelativePath "release")) {
    $ReleaseOut = "release-build"
    Write-Host "   Building to $ReleaseOut/ because release/ could not be cleared (often Cursor or AV holding app.asar)." -ForegroundColor Yellow
    if (-not (Remove-BuildOutputDir -RelativePath $ReleaseOut -Hint "Close anything using desktop\$ReleaseOut\.")) {
        Write-Err "Cannot clear '$ReleaseOut' either. Quit Babo/Electron, close Explorer on that folder, retry."
    }
}

$ebArgs = @("--win", "nsis", "--publish", "never", "-c.directories.output=$ReleaseOut")
npx electron-builder @ebArgs
if ($LASTEXITCODE -ne 0) { Write-Err "electron-builder (Windows) failed" }

$installer = Get-ChildItem "$ReleaseOut\*.exe" | Where-Object { $_.Name -notlike "*uninstaller*" -and $_.Name -like "*Setup*" } | Select-Object -First 1
$blockmap  = Get-ChildItem "$ReleaseOut\*.blockmap" | Select-Object -First 1
$latestYml = "$ReleaseOut\latest.yml"

if (-not $installer) { Write-Err "Installer .exe not found in $ReleaseOut\" }
if (-not $blockmap)  { Write-Err "Blockmap not found in $ReleaseOut\" }
if (-not (Test-Path $latestYml)) { Write-Err "latest.yml not found in $ReleaseOut\" }

Write-Ok "Installer : $($installer.Name)"
Write-Ok "Blockmap  : $($blockmap.Name)"

$ymlContent = Get-Content $latestYml -Raw
$actualName = $installer.Name
if ($ymlContent -notmatch [regex]::Escape($actualName)) {
    $ymlContent = $ymlContent -replace 'Babo[-. ]Setup[-. ]\d+\.\d+\.\d+\.exe', $actualName
    Set-Content $latestYml $ymlContent -NoNewline
    Write-Ok "latest.yml patched"
}

# ── 4. Git commit + push ──────────────────────────────────────────────────

if (-not $SkipGit) {
    Write-Step "Committing and pushing version"
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

# ── 5. Sync repo to Mac and build (no git pull; avoids GitHub auth on Mac) ───

Write-Step "Syncing repo snapshot to Mac"
Push-Location ..
$snapshotTar = "desktop/repo-snapshot.tar.gz"
if (Test-Path $snapshotTar) { Remove-Item $snapshotTar -Force }
tar --exclude=.git --exclude=node_modules --exclude=desktop/release --exclude=desktop/release-build --exclude=desktop/release-mac -czf $snapshotTar frontend desktop nls server requirements.txt requirements-desktop.txt 2>$null
if ($LASTEXITCODE -ne 0) { Pop-Location; Write-Err "tar (repo snapshot) failed" }
Pop-Location
scp -o BatchMode=yes "repo-snapshot.tar.gz" "${MacHost}:/tmp/repo-snapshot.tar.gz"
if ($LASTEXITCODE -ne 0) { Write-Err "SCP repo snapshot to Mac failed" }
Remove-Item "repo-snapshot.tar.gz" -Force -ErrorAction SilentlyContinue
Write-Ok "Snapshot uploaded"

Write-Step "Building on Mac ($MacHost) via desktop/build-mac.sh"
# Pre-extract snapshot so the LATEST build-mac.sh is on disk before we pipe it to bash.
# Without this, `tr < desktop/build-mac.sh` reads the Mac's stale copy (pre-snapshot).
$macCmd = 'zsh -l -c ''source ~/.zshrc 2>/dev/null; source ~/.nvm/nvm.sh 2>/dev/null; cd ' + $MacRepoPath + ' && tar xzf /tmp/repo-snapshot.tar.gz 2>/dev/null; tr -d "\r" < desktop/build-mac.sh | bash -s /tmp/repo-snapshot.tar.gz; EXIT_CODE=$?; echo "::BUILD_EXIT_CODE::${EXIT_CODE}"; exit $EXIT_CODE'''
$macBuildOutput = ssh -o BatchMode=yes -o ConnectTimeout=10 $MacHost $macCmd 2>&1
$macSshExit = $LASTEXITCODE
$lines = $macBuildOutput | Out-String
$lineArr = $lines -split "`n"

# Extract the actual build exit code from the sentinel line (pipe can mask failures)
$sentinelLine = $lineArr | Where-Object { $_ -match '::BUILD_EXIT_CODE::(\d+)' } | Select-Object -Last 1
$buildExitCode = if ($sentinelLine -match '::BUILD_EXIT_CODE::(\d+)') { [int]$Matches[1] } else { $macSshExit }

if ($buildExitCode -ne 0 -or $macSshExit -ne 0) {
    Write-Host "`n   --- Mac build output (last 80 lines) ---" -ForegroundColor Yellow
    $tail = if ($lineArr.Count -gt 80) { $lineArr[-80..-1] } else { $lineArr }
    $tail | ForEach-Object { Write-Host "   $_" }
    Write-Err "Mac build or tar failed (ssh=$macSshExit, build=$buildExitCode)"
}
Write-Ok "Mac build done"

# Verify the tarball actually exists on the Mac before trying to download it
$tarCheck = ssh -o BatchMode=yes -o ConnectTimeout=10 $MacHost "test -f /tmp/babo-release-mac.tar.gz && echo EXISTS || echo MISSING" 2>&1
if ("$tarCheck" -notmatch "EXISTS") {
    Write-Host "`n   --- Mac build output (last 80 lines) ---" -ForegroundColor Yellow
    $tail = if ($lineArr.Count -gt 80) { $lineArr[-80..-1] } else { $lineArr }
    $tail | ForEach-Object { Write-Host "   $_" }
    Write-Err "/tmp/babo-release-mac.tar.gz not found on Mac despite build reporting success. Check Mac build output above."
}

Write-Step "Downloading Mac artifacts"
$releaseMacDir = "release-mac"
if (-not (Remove-BuildOutputDir -RelativePath $releaseMacDir -Hint "Close anything browsing desktop\release-mac\.")) {
    Write-Err "Cannot clear '$releaseMacDir'. Close apps or Explorer windows using that folder, then retry."
}
New-Item -ItemType Directory -Path $releaseMacDir | Out-Null

scp -o BatchMode=yes "${MacHost}:/tmp/babo-release-mac.tar.gz" "$releaseMacDir/mac.tar.gz"
if ($LASTEXITCODE -ne 0) { Write-Err "SCP from Mac failed" }

# Extract (tar on Windows 10+ is available)
$tarOut = tar -xzf "$releaseMacDir/mac.tar.gz" -C $releaseMacDir 2>&1
if ($LASTEXITCODE -ne 0) { Write-Err "tar extract failed: $tarOut" }
Remove-Item "$releaseMacDir/mac.tar.gz" -Force

# Clean up temp on Mac
ssh -o BatchMode=yes $MacHost "rm -f /tmp/babo-release-mac.tar.gz" 2>$null

$macArtifacts = @(Get-ChildItem "$releaseMacDir\*.dmg" -ErrorAction SilentlyContinue) +
                @(Get-ChildItem "$releaseMacDir\*.zip" -ErrorAction SilentlyContinue)
Write-Ok "Mac artifacts: $($macArtifacts.Name -join ', ')"

# Verify SHA512 hashes of Mac DMGs and ZIPs match latest-mac.yml after transfer
Write-Step "Verifying Mac artifact checksums after transfer"
$latestMacYml = "$releaseMacDir\latest-mac.yml"
if (Test-Path $latestMacYml) {
    $ymlText = Get-Content $latestMacYml -Raw
    $hashOk = $true
    foreach ($artifact in $macArtifacts) {
        $hashBytes = [System.Security.Cryptography.SHA512]::Create().ComputeHash(
            [System.IO.File]::ReadAllBytes($artifact.FullName)
        )
        $b64Hash = [Convert]::ToBase64String($hashBytes)
        if ($ymlText -match [regex]::Escape($b64Hash)) {
            Write-Ok "SHA512 OK: $($artifact.Name)"
        } else {
            Write-Host "   MISMATCH: $($artifact.Name)" -ForegroundColor Red
            Write-Host "   Got:      $b64Hash" -ForegroundColor Yellow
            $hashOk = $false
        }
    }
    if (-not $hashOk) {
        Write-Err "Mac artifact checksums do not match latest-mac.yml. The tar/scp transfer may have corrupted files. Re-run the build."
    }
} else {
    Write-Host "   WARNING: latest-mac.yml not found in Mac artifacts" -ForegroundColor Yellow
}

# Copy Mac artifacts next to Windows artifacts (same folder gh uses)
Write-Step "Copying Mac builds into $ReleaseOut/"
Copy-Item "$releaseMacDir\*" -Destination "$ReleaseOut\" -Force
Write-Ok "$ReleaseOut/ now contains Windows + Mac builds"

# ── 6. Create GitHub Release (Windows + Mac) ─────────────────────────────────

Write-Step "Creating GitHub Release $tag"

$existingRelease = gh release view $tag -R umbecanessa/babo 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "   Release $tag already exists - deleting it first" -ForegroundColor Yellow
    gh release delete $tag -R umbecanessa/babo --yes
}

# Only upload actual release artifacts (exclude debug files, resource forks)
$assets = Get-ChildItem "$ReleaseOut\*" -File |
    Where-Object {
        $_.Name -notlike "._*" -and
        $_.Name -notlike "default._*" -and
        $_.Name -ne "builder-debug.yml" -and
        $_.Name -ne "builder-effective-config.yaml"
    } |
    ForEach-Object { $_.FullName }

Write-Ok "Uploading $($assets.Count) assets: $(($assets | Split-Path -Leaf) -join ', ')"

gh release create $tag `
    -R umbecanessa/babo `
    --title $tag `
    --notes "Release $tag (Windows + macOS)" `
    --latest `
    $assets

if ($LASTEXITCODE -ne 0) { Write-Err "GitHub release creation failed" }

Write-Ok "Release published: https://github.com/umbecanessa/babo/releases/tag/$tag"

# ── Done ──────────────────────────────────────────────────────────────────

Write-Host "`n[OK] Release $tag complete (Windows + macOS).`n" -ForegroundColor Green
Write-Host "Users will see the update notification; Mac builds are in the same release." -ForegroundColor DarkGray
