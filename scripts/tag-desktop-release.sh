#!/usr/bin/env bash
# Bump desktop/package.json, commit, tag vX.Y.Z, push — triggers Release Desktop on GitHub Actions.
#
# Usage (from repo root):
#   ./scripts/tag-desktop-release.sh              # patch bump
#   ./scripts/tag-desktop-release.sh --minor
#   ./scripts/tag-desktop-release.sh --version 1.9.7
#   ./scripts/tag-desktop-release.sh --dry-run

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PKG_JSON="$REPO_ROOT/desktop/package.json"
BRANCH="${BRANCH:-main}"
BUMP="patch"
VERSION=""
DRY_RUN=0

usage() {
  sed -n '2,9p' "$0" | sed 's/^# \?//'
  exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --patch) BUMP="patch"; shift ;;
    --minor) BUMP="minor"; shift ;;
    --major) BUMP="major"; shift ;;
    --version) VERSION="${2:?}"; shift 2 ;;
    --branch) BRANCH="${2:?}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage 0 ;;
    *) echo "Unknown option: $1" >&2; usage 1 ;;
  esac
done

cd "$REPO_ROOT"

step() { printf '\n>> %s\n' "$*"; }
ok() { printf '   %s\n' "$*"; }
die() { printf '   %s\n' "$*" >&2; exit 1; }

run_git() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '   [dry-run] git %s\n' "$*"
    return 0
  fi
  git "$@"
}

get_version() {
  node -p "require('$PKG_JSON').version"
}

set_version() {
  local ver="$1"
  node <<NODE
const fs = require('fs');
const p = '$PKG_JSON';
const raw = fs.readFileSync(p, 'utf8');
fs.writeFileSync(p, raw.replace(/"version":\s*"[^"]*"/, '"version": "$ver"'));
NODE
}

bump_version() {
  local current="$1" kind="$2"
  node -e "
const [a,b,c]=process.argv[1].split('.').map(Number);
let v;
if (process.argv[2]==='major') v=[a+1,0,0];
else if (process.argv[2]==='minor') v=[a,b+1,0];
else v=[a,b,c+1];
console.log(v.join('.'));
" "$current" "$kind"
}

step "Pre-flight"
command -v git >/dev/null || die "git not found"
command -v node >/dev/null || die "node not found"

current_branch="$(git rev-parse --abbrev-ref HEAD)"
[[ "$current_branch" == "$BRANCH" ]] || die "On branch '$current_branch'; checkout '$BRANCH' first."

is_ignored_release_path() {
  local path="${1//\\//}"
  case "$path" in
    desktop/dist-electron/*|desktop/release/*|desktop/release-build/*|desktop/release-mac/*) return 0 ;;
    *) return 1 ;;
  esac
}

has_blocking_dirty() {
  local line path
  while IFS= read -r line; do
    path="${line#?? }"
    path="${path#* -> }"
    path="${path//\\//}"
    if ! is_ignored_release_path "$path"; then
      return 0
    fi
  done < <(git status --porcelain --untracked-files=no)
  return 1
}

if [[ "$DRY_RUN" -eq 0 ]] && has_blocking_dirty; then
  git status --porcelain --untracked-files=no | while IFS= read -r line; do
    path="${line#?? }"
    path="${path#* -> }"
    path="${path//\\//}"
    is_ignored_release_path "$path" || echo "$line"
  done
  die "Commit or stash other changes before releasing."
fi

old_version="$(get_version)"
if [[ -n "$VERSION" ]]; then
  [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "Version must be X.Y.Z"
  new_version="$VERSION"
else
  new_version="$(bump_version "$old_version" "$BUMP")"
fi

tag="v${new_version}"
ok "Current version : $old_version"
ok "Release version : $new_version"
ok "Tag             : $tag"
ok "Branch          : $BRANCH"

if [[ "$DRY_RUN" -eq 0 ]]; then
  if git ls-remote --tags origin "refs/tags/${tag}" | grep -q .; then
    die "Tag $tag already exists on origin."
  fi
  if git tag -l "$tag" | grep -q .; then
    die "Local tag $tag exists. Remove with: git tag -d $tag"
  fi
fi

step "Updating desktop/package.json"
if [[ "$DRY_RUN" -eq 1 ]]; then
  ok "[dry-run] set version -> $new_version"
else
  set_version "$new_version"
  ok "desktop/package.json -> $new_version"
fi

step "Commit, push branch, tag, push tag"
run_git add desktop/package.json
run_git commit -m "release: $tag"
run_git push origin "$BRANCH"
run_git tag -a "$tag" -m "release: $tag"
run_git push origin "$tag"

printf '\n'
if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "Dry run complete (no changes made)."
else
  echo "Tagged and pushed $tag on $BRANCH."
  echo "CI: https://github.com/umbecanessa/babo/actions/workflows/release-desktop.yml"
  echo "Release: https://github.com/umbecanessa/babo/releases/tag/$tag"
fi
printf '\n'
