#!/usr/bin/env bash
# Build Babo Desktop for macOS and pack release artifacts into /tmp/babo-release-mac.tar.gz
#
# Usage:
#   ./desktop/build-mac.sh              - build current tree (e.g. after git pull)
#   ./desktop/build-mac.sh /path/to.tar.gz  - extract snapshot tarball into repo, then build (no git needed)
#
# Run from repo root: ./desktop/build-mac.sh [snapshot.tar.gz]

set -e

# When run via stdin (e.g. tr -d "\r" < desktop/build-mac.sh | bash -s), BASH_SOURCE[0] is not the script path; use PWD (invoker must cd to repo root first).
if [[ -n "${BASH_SOURCE[0]}" && "${BASH_SOURCE[0]}" != "-" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  REPO_ROOT="$(dirname "$SCRIPT_DIR")"
else
  REPO_ROOT="${PWD}"
  SCRIPT_DIR="${REPO_ROOT}/desktop"
fi
cd "$REPO_ROOT"

# Optional: extract snapshot from Windows (avoids git pull / GitHub auth on Mac)
if [[ -n "$1" && -f "$1" ]]; then
  echo ">> Extracting snapshot from $1..."
  # --no-mac-metadata: prevent AppleDouble ._* resource fork files from leaking
  # into the extracted tree (they cause spurious release assets like default._*.dmg)
  tar xzf "$1" --no-mac-metadata -C "$REPO_ROOT" 2>/dev/null || tar xzf "$1" -C "$REPO_ROOT"
  cd "$REPO_ROOT/desktop"
  echo ">> Installing deps (npm ci)..."
  npm ci
  xattr -cr node_modules 2>/dev/null || true
  chmod -R +x node_modules 2>/dev/null || true
fi

cd "$SCRIPT_DIR"

# ── Code Signing & Notarization ──────────────────────────────────
# If a Developer ID certificate is in the Keychain, electron-builder
# will auto-discover and sign.  Set CSC_IDENTITY_AUTO_DISCOVERY=false
# to skip signing (unsigned dev builds).
#
# For notarization, set these env vars (or in ~/.zshrc):
#   APPLE_ID            - your Apple ID email
#   APPLE_APP_SPECIFIC_PASSWORD - app-specific password from appleid.apple.com
#   APPLE_TEAM_ID       - your 10-char team ID from developer.apple.com
#
# To force unsigned build: CSC_IDENTITY_AUTO_DISCOVERY=false ./desktop/build-mac.sh

if security find-identity -v -p codesigning 2>/dev/null | grep -q "Developer ID"; then
  echo ">> Code signing identity found — signing enabled"
  export CSC_IDENTITY_AUTO_DISCOVERY=true
  if [[ -n "$APPLE_ID" && -n "$APPLE_APP_SPECIFIC_PASSWORD" && -n "$APPLE_TEAM_ID" ]]; then
    echo ">> Notarization credentials found — notarization enabled"
  else
    echo ">> Notarization skipped (set APPLE_ID, APPLE_APP_SPECIFIC_PASSWORD, APPLE_TEAM_ID)"
  fi
else
  echo ">> No Developer ID certificate found — building unsigned"
  export CSC_IDENTITY_AUTO_DISCOVERY=false
fi

# Prevent macOS from creating ._* AppleDouble resource fork files in tarballs
export COPYFILE_DISABLE=1

echo ">> Building macOS app in $(pwd)..."
npm run dist:mac

if [[ ! -d release ]] || [[ -z "$(ls -A release 2>/dev/null)" ]]; then
  echo "ERROR: desktop/release is missing or empty after build"
  exit 1
fi

VERSION="$(node -p "require('./package.json').version")"
echo ">> Creating tarball (version $VERSION only)..."
cd release

# Clean up macOS resource fork files that shouldn't be in the release
rm -f ._* default._* 2>/dev/null

FILES=()
for f in "Babo-${VERSION}-mac"*.dmg "Babo-${VERSION}-mac"*.zip "Babo-${VERSION}-mac"*.blockmap latest-mac.yml; do
  # Skip AppleDouble resource fork files
  [[ "$f" == ._* ]] && continue
  [[ "$f" == default._* ]] && continue
  [[ -f "$f" ]] && FILES+=("$f")
done
if [[ ${#FILES[@]} -eq 0 ]]; then
  echo "ERROR: no current-version artifacts found (Babo-${VERSION}-mac*)" >&2
  exit 1
fi

# Verify SHA512 hashes in latest-mac.yml match actual artifacts BEFORE packaging.
# This catches any electron-builder or filesystem issues at the source.
echo ">> Verifying SHA512 checksums..."
VERIFY_FAIL=0
for artifact in "Babo-${VERSION}-mac"*.dmg "Babo-${VERSION}-mac"*.zip; do
  [[ -f "$artifact" ]] || continue
  ACTUAL_HASH=$(shasum -a 512 "$artifact" | awk '{print $1}' | xxd -r -p | base64)
  if grep -q "$ACTUAL_HASH" latest-mac.yml; then
    echo "   OK: $artifact"
  else
    echo "   MISMATCH: $artifact (hash $ACTUAL_HASH not found in latest-mac.yml)" >&2
    VERIFY_FAIL=1
  fi
done
if [[ $VERIFY_FAIL -ne 0 ]]; then
  echo "ERROR: SHA512 verification failed. latest-mac.yml does not match artifacts." >&2
  echo "       This would cause update failures for users." >&2
  exit 1
fi

rm -f /tmp/babo-release-mac.tar.gz 2>/dev/null
tar czf /tmp/babo-release-mac.tar.gz "${FILES[@]}"
if [[ ! -f /tmp/babo-release-mac.tar.gz ]]; then
  echo "ERROR: tar succeeded but /tmp/babo-release-mac.tar.gz does not exist" >&2
  exit 1
fi
echo ">> Done: /tmp/babo-release-mac.tar.gz (${#FILES[@]} files, $(du -h /tmp/babo-release-mac.tar.gz | cut -f1))"
