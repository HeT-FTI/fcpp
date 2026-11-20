#!/usr/bin/env bash
# Local and CI MegaLinter runner: same config, same pinned version, optional container engine.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

CONTAINER_ENGINE="${CONTAINER_ENGINE:-docker}"
MEGALINTER_VERSION="v10.2.0"
MEGALINTER_RUNNER="10.2.0"
NODE_MIN_MAJOR=22
NODE_MIN_MINOR=18

for cmd in "$CONTAINER_ENGINE" npx node; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "error: '$cmd' not found." >&2
    echo "       MegaLinter Runner needs a container engine (docker/podman) and Node.js >= ${NODE_MIN_MAJOR}.${NODE_MIN_MINOR}." >&2
    exit 1
  fi
done

node_version="$(node -p 'process.versions.node')"
node_major="${node_version%%.*}"
node_rest="${node_version#*.}"
node_minor="${node_rest%%.*}"
if (( node_major < NODE_MIN_MAJOR || (node_major == NODE_MIN_MAJOR && node_minor < NODE_MIN_MINOR) )); then
  echo "error: Node.js ${node_version} is too old; mega-linter-runner ${MEGALINTER_RUNNER} needs >= ${NODE_MIN_MAJOR}.${NODE_MIN_MINOR}." >&2
  exit 1
fi

FIX_FLAG=""
FILES=()
for arg in "$@"; do
  case "$arg" in
    --fix) FIX_FLAG="--fix" ;;
    -h|--help)
      echo "Usage: .github/misc/run-megalinter.sh [--fix] [path ...]"
      echo "  no arguments: lint the whole codebase"
      echo "  --fix: apply fixes locally"
      echo "  path: lint selected files only"
      exit 0
      ;;
    *) FILES+=("$arg") ;;
  esac
done

echo "▶ Running MegaLinter (config: .github/misc/.mega-linter.yml, engine: $CONTAINER_ENGINE, version: $MEGALINTER_VERSION)"

exec npx --yes "mega-linter-runner@${MEGALINTER_RUNNER}" \
  --container-engine "$CONTAINER_ENGINE" \
  --release "$MEGALINTER_VERSION" \
  -e "MEGALINTER_CONFIG=.github/misc/.mega-linter.yml" \
  $FIX_FLAG \
  "${FILES[@]}"
