#!/usr/bin/env bash
# OpenReef runner for Apple containers (macOS 26+, `container` CLI).
#
# The same OCI image as Docker — this script exists so users who prefer
# Apple's native container stack (lightweight per-container VMs, no Docker
# daemon) get the same one-command experience as `docker compose up`.
#
# Usage:
#   ./scripts/run-apple-container.sh [start]   build (if needed) + start
#   ./scripts/run-apple-container.sh stop
#   ./scripts/run-apple-container.sh logs
#   ./scripts/run-apple-container.sh wipe      remove container + data dir
#
# Overrides: OPENREEF_NAME / OPENREEF_IMAGE / OPENREEF_DATA / OPENREEF_ENV /
# OPENREEF_CPUS / OPENREEF_MEMORY / OPENREEF_REBUILD=1
set -euo pipefail

NAME="${OPENREEF_NAME:-openreef}"
IMAGE="${OPENREEF_IMAGE:-openreef:local}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="${OPENREEF_DATA:-$HOME/openreef-data}"
CPUS="${OPENREEF_CPUS:-4}"
MEMORY="${OPENREEF_MEMORY:-8G}"
ENV_FILE="${OPENREEF_ENV:-$REPO_ROOT/.env}"

die() { echo "ERROR: $*" >&2; exit 1; }

check_env() {
    [ -f "$ENV_FILE" ] || die "env file not found: $ENV_FILE (cp .env.example .env and fill in your LLM config)"
    if grep -vE '^[[:space:]]*#' "$ENV_FILE" | grep -Eqi "your_|change_me|placeholder"; then
        die "env file has placeholder values in active lines: $ENV_FILE"
    fi
}

start() {
    command -v container >/dev/null || die "Apple 'container' CLI not found. Install it (https://github.com/apple/container) or use Docker instead: docker compose up -d --build"
    check_env

    if [ "${OPENREEF_REBUILD:-0}" = "1" ] || ! container image inspect "$IMAGE" >/dev/null 2>&1; then
        echo "Building $IMAGE from $REPO_ROOT ..."
        container build -t "$IMAGE" "$REPO_ROOT"
    fi

    # Recreate on every start so env/config changes always apply.
    container stop "$NAME" >/dev/null 2>&1 || true
    container rm "$NAME" >/dev/null 2>&1 || true
    mkdir -p "$DATA_DIR"

    local -a run_args=(
        -d --name "$NAME" --init
        --env-file "$ENV_FILE"
        --cpus "$CPUS" --memory "$MEMORY"
        -p 127.0.0.1:3000:3000
        -p 127.0.0.1:5001:5001
        -v "$DATA_DIR:/app/backend/uploads"
    )
    if [ -f "$REPO_ROOT/llm.config.json" ]; then
        run_args+=( -v "$REPO_ROOT/llm.config.json:/app/backend/llm.config.json:ro" )
        echo "Mounting LLM provider config: $REPO_ROOT/llm.config.json"
    fi

    container run "${run_args[@]}" "$IMAGE"

    cat <<EOF

OpenReef is starting in container: $NAME
  UI:       http://127.0.0.1:3000
  Backend:  http://127.0.0.1:5001
  Data dir: $DATA_DIR
  Logs:     $0 logs
EOF
}

case "${1:-start}" in
    start)  start ;;
    stop)   container stop "$NAME" ;;
    logs)   container logs -f "$NAME" ;;
    wipe)
        container stop "$NAME" >/dev/null 2>&1 || true
        container rm "$NAME" >/dev/null 2>&1 || true
        rm -rf "$DATA_DIR"
        echo "Removed container '$NAME' and data dir $DATA_DIR (image kept)"
        ;;
    *) die "unknown command: $1 (expected: start | stop | logs | wipe)" ;;
esac
