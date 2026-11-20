#!/usr/bin/env bash
# Apply the checked-in main-branch ruleset; safe to re-run (create or update by name).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RULESET_FILE="$ROOT_DIR/.github/rulesets/main.json"
DRY_RUN=""

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN="--dry-run" ;;
    -h|--help)
      echo "Usage: .github/misc/apply-main-rules.sh [--dry-run]"
      exit 0
      ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

command -v gh >/dev/null 2>&1 || { echo "error: gh CLI is required" >&2; exit 1; }
command -v jq >/dev/null 2>&1 || { echo "error: jq is required" >&2; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "error: run gh auth login first" >&2; exit 1; }

REPO="${GITHUB_REPOSITORY:-$(git -C "$ROOT_DIR" remote get-url origin)}"
REPO="${REPO#git@github.com:}"
REPO="${REPO#https://github.com/}"
REPO="${REPO%.git}"

existing="$(gh api "repos/$REPO/rulesets" --jq '.[] | select(.name == "main") | .id' 2>/dev/null || true)"

if [ -n "$DRY_RUN" ]; then
  echo "would apply .github/rulesets/main.json to $REPO"
  echo "would set squash-only merges on $REPO"
  exit 0
fi

gh api -X PATCH "repos/$REPO" \
  -F allow_merge_commit=false \
  -F allow_rebase_merge=false \
  -F allow_squash_merge=true \
  -F squash_merge_commit_title=PR_TITLE \
  -F squash_merge_commit_message=BLANK >/dev/null

if [ -n "$existing" ]; then
  echo "updating ruleset $existing on $REPO"
  gh api -X PUT "repos/$REPO/rulesets/$existing" --input "$RULESET_FILE" >/dev/null
else
  echo "creating ruleset on $REPO"
  gh api -X POST "repos/$REPO/rulesets" --input "$RULESET_FILE" >/dev/null
fi

gh api "repos/$REPO/rulesets" --jq '.[] | select(.name == "main") | "\(.name): \(.enforcement) (\(.id))"'
