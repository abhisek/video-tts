#!/usr/bin/env bash
# Set up (or reuse) the Python venv, install deps, then generate the voice-over video.
# Usage: ./start.sh <video> <script.txt> [options]   (see ./start.sh --help)
set -euo pipefail

# Don't cd: video/script paths passed in are relative to the caller's directory.
DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$DIR/.venv"
STAMP="$VENV/.requirements.sha256"

# Python version comes from .tool-versions via mise.
if ! command -v mise >/dev/null; then
  echo "mise not found; it is needed to provide the Python in .tool-versions" >&2
  exit 1
fi
PYTHON="$(cd "$DIR" && mise install --yes python >&2 && mise which python)"
WANT="$(awk '$1 == "python" { print $2 }' "$DIR/.tool-versions")"

# Recreate the venv if it is missing or was built with a different Python.
if [[ -x "$VENV/bin/python" ]]; then
  HAVE="$("$VENV/bin/python" -c 'import platform; print(platform.python_version())')"
  if [[ "$HAVE" != "$WANT" ]]; then
    echo "venv uses Python $HAVE, want $WANT; recreating"
    rm -rf "$VENV"
  fi
fi
if [[ ! -x "$VENV/bin/python" ]]; then
  echo "Creating venv with $PYTHON"
  "$PYTHON" -m venv "$VENV"
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"

# Only reinstall when requirements.txt changes.
REQ_HASH="$(sha256sum "$DIR/requirements.txt" | cut -d' ' -f1)"
if [[ ! -f "$STAMP" || "$(cat "$STAMP")" != "$REQ_HASH" ]]; then
  python -m pip install --upgrade pip
  python -m pip install -r "$DIR/requirements.txt"
  echo "$REQ_HASH" > "$STAMP"
else
  echo "Dependencies up to date"
fi

exec python "$DIR/make_vo.py" "$@"
