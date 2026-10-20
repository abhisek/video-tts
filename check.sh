#!/usr/bin/env bash
# Check that everything start.sh and make_vo.py need is present. Changes nothing.
# Exit code: 0 = ready, 1 = something required is missing.
set -uo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
FAIL=0
ok()   { printf '  \033[32mok\033[0m    %s\n' "$*"; }
warn() { printf '  \033[33mwarn\033[0m  %s\n' "$*"; }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$*"; FAIL=1; }

echo "Tools"
WANT="$(awk '$1 == "python" { print $2 }' "$DIR/.tool-versions")"
if command -v mise >/dev/null; then
  ok "mise $(mise --version 2>/dev/null | awk '{print $1}')"
  if HAVE_PY="$(cd "$DIR" && mise which python 2>/dev/null)"; then
    ok "python $WANT from .tool-versions ($HAVE_PY)"
  else
    warn "python $WANT not installed yet; start.sh will run 'mise install'"
  fi
else
  bad "mise not found (provides the Python pinned in .tool-versions): https://mise.jdx.dev"
fi

for t in ffmpeg ffprobe; do
  if command -v "$t" >/dev/null; then ok "$t $("$t" -version | awk 'NR==1 {print $3}')"; else bad "$t not found"; fi
done

if command -v ffmpeg >/dev/null; then
  ENCODERS="$(ffmpeg -hide_banner -encoders 2>/dev/null)"
  FILTERS="$(ffmpeg -hide_banner -filters 2>/dev/null)"
  for e in libx264 libmp3lame aac mjpeg; do
    grep -qE "^ [A-Z.]{6} $e " <<<"$ENCODERS" && ok "encoder $e" || bad "ffmpeg encoder $e missing"
  done
  for f in loudnorm adelay amix apad tpad concat xstack showinfo; do
    grep -qE "^ [A-Z.]{2,3} $f " <<<"$FILTERS" && ok "filter $f" || bad "ffmpeg filter $f missing"
  done
fi

echo "Environment"
if [[ -x "$DIR/.venv/bin/python" ]]; then
  VENV_PY="$("$DIR/.venv/bin/python" -c 'import platform; print(platform.python_version())')"
  if [[ "$VENV_PY" == "$WANT" ]]; then ok ".venv (Python $VENV_PY)"; else warn ".venv uses Python $VENV_PY, want $WANT; start.sh will recreate it"; fi
  if [[ -f "$DIR/.venv/.requirements.sha256" && "$(cat "$DIR/.venv/.requirements.sha256")" == "$(sha256sum "$DIR/requirements.txt" | cut -d' ' -f1)" ]]; then
    ok "dependencies match requirements.txt"
  else
    warn "dependencies out of date; start.sh will install them"
  fi
else
  warn ".venv missing; start.sh will create it (first run downloads ~1 GB: Python, torch CPU, spaCy)"
fi

HF_CACHE="${HF_HOME:-$HOME/.cache/huggingface}/hub/models--hexgrad--Kokoro-82M"
if [[ -d "$HF_CACHE" ]]; then ok "Kokoro model cached"; else warn "Kokoro model not cached; first run downloads ~330 MB from huggingface.co"; fi

AVAIL_GB="$(df -Pk "$DIR" | awk 'NR==2 {printf "%d", $4/1024/1024}')"
if (( AVAIL_GB >= 3 )); then ok "${AVAIL_GB} GB free disk"; else warn "only ${AVAIL_GB} GB free disk; setup needs ~2-3 GB"; fi

echo
if (( FAIL )); then echo "Not ready: fix the FAIL items above."; else echo "Ready. Run ./start.sh <video> <script> [options]"; fi
exit "$FAIL"
