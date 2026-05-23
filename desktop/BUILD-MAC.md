# Building Babo Desktop for macOS

macOS builds **must be done on a Mac**; electron-builder cannot produce macOS apps on Windows or Linux.

## Prerequisites

1. **macOS** (Ventura or later recommended)
2. **Node.js** (LTS, e.g. 20.x) and npm
3. **Xcode Command Line Tools** (required for native modules):
   ```bash
   xcode-select --install
   ```
4. **GitHub CLI** (`gh`) – for creating releases and uploading artifacts:
   ```bash
   brew install gh
   gh auth login
   ```

## Remote build from Windows (release-all.ps1)

The script SSHs to the Mac and runs `git pull`. The Mac must be logged in to GitHub so that works without a prompt. Easiest is to log in once with the GitHub CLI.

On the Mac, run once:

```bash
# 1. Log in to GitHub (run once on the Mac; use HTTPS, then "Login with a web browser")
gh auth login

# 2. Use HTTPS so git pull uses your stored token
cd ~/Documents/GitHub/babo
git remote set-url origin https://github.com/umbecanessa/babo.git
```

Once gh is logged in and the remote is HTTPS, the release script's `release-all.ps1`’s `git pull` will work.

## Build (unsigned, for testing)

```bash
cd desktop
npm ci
export CSC_IDENTITY_AUTO_DISCOVERY=false
npm run dist:mac
```

Output:

- **Intel:** `release/Babo-<version>-mac.dmg` (x64)
- **Apple Silicon:** `release/Babo-<version>-mac-arm64.dmg` (arm64)

Both are produced when building on Apple Silicon; on Intel you get x64 only unless you cross-build.

## Signing & notarization (for distribution)

To avoid “unidentified developer” / Gatekeeper blocks:

1. **Apple Developer account** (enrollment in Apple Developer Program)
2. **Developer ID Application** certificate in Keychain
3. **Notarization** – set before building:
   ```bash
   export CSC_LINK=/path/to/DeveloperIDApplication.p12
   export CSC_KEY_PASSWORD=your-p12-password
   export APPLE_ID=your@email.com
   export APPLE_APP_SPECIFIC_PASSWORD=app-specific-password
   export APPLE_TEAM_ID=YourTeamID
   npm run dist:mac
   ```

Without these, the app can still be built and run locally (e.g. right‑click → Open to bypass Gatekeeper once).

## Releasing to GitHub

After building on macOS:

1. Bump version in `package.json` if needed (or use the release script’s version logic).
2. Commit and push the version bump.
3. Create or reuse a GitHub release and upload the DMG(s) and any `latest-mac.yml`:

   ```bash
   cd desktop
   VERSION=$(node -p "require('./package.json').version")
   TAG="v$VERSION"
   gh release create "$TAG" --repo umbecanessa/babo --latest \
     "release/Babo-${VERSION}-mac.dmg" \
     "release/Babo-${VERSION}-mac-arm64.dmg" \
     # add latest-mac.yml if electron-builder produced it
   ```

   Or use a small shell script that mirrors `release.ps1` (bump → build → commit → `gh release create` with all artifacts).

## Assets

- **Icon:** `desktop/build/icon.png` (512×512 or 1024×1024 for retina). Already present for Windows; reuse for mac.
- **Config:** `electron-builder.yml` already defines the mac target (dmg, x64 + arm64).

## Summary

| Step              | Command / requirement                          |
|-------------------|-------------------------------------------------|
| Run on            | macOS only                                      |
| Install deps      | `npm ci` in repo root + `cd desktop && npm ci`  |
| Unsigned build    | `CSC_IDENTITY_AUTO_DISCOVERY=false npm run dist:mac` |
| Signed/notarized  | Set `CSC_*` and `APPLE_*` env vars, then build  |
| Release           | `gh release create` with DMG(s) and `latest-mac.yml` |
