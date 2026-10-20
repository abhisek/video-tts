# AGENTS.md

Adds a narrated voice-over (local Kokoro TTS, CPU only) and a share-preview cover image to a video, driven by a
timed text script. The end-to-end workflow lives in the `voiceover-video` skill
(`.claude/skills/voiceover-video/SKILL.md`). Use it whenever a user asks for a voice-over, narration or cover on a
video.

## Layout

| Path | What it is |
|---|---|
| `check.sh` | Read-only preflight: mise, Python from `.tool-versions`, ffmpeg/ffprobe plus the encoders and filters we use, venv state, model cache, disk space. Exits 1 if something required is missing. |
| `start.sh` | Entry point. Installs Python via mise, creates or reuses `.venv`, runs `pip install -r requirements.txt` only when that file changes, then runs `make_vo.py "$@"`. Works from any directory. |
| `make_vo.py` | Everything else, in one file: script parsing, scene detection, `--check`, cover candidates, Kokoro synthesis, ffmpeg muxing. |
| `requirements.txt` | Pinned dependencies. torch comes from the CPU wheel index. |
| `.tool-versions` | Python version (3.12.x; see "Gotchas"). |
| `out/<video name>/` | Generated clips, cover candidates and lexicons. Disposable. |

Outputs go next to the input video as `<video name> - VO.mp4` unless `-o` is given.

## Commands

```bash
./check.sh                                   # is this machine ready?
./start.sh VIDEO --scenes                    # scene changes + script skeleton
./start.sh VIDEO --cover-candidates          # candidate cover frames + contact sheet
./start.sh VIDEO SCRIPT --check              # timing vs scenes, length estimates, pronunciation
./start.sh VIDEO SCRIPT --cover IMG --strict # generate the final video
./start.sh VIDEO --help                      # all options
```

There's no test suite. To smoke-test a change, run `--scenes`, `--check` and a full generate on a short video, then
check the output with ffprobe (streams, duration, `attached_pic`) and ebur128 loudness, as in step 6 of the skill.

## Conventions

- Keep `make_vo.py` a single file using only the stdlib, numpy, soundfile and kokoro. Call ffmpeg/ffprobe through
  `subprocess` with argument lists, never `shell=True`.
- Python only through `start.sh` / `.venv`. Don't install packages globally, and don't add a dependency without
  pinning it in `requirements.txt`.
- Print progress the way the code already does (`  <clip>: <len>s / max <n>s at speed <x>  OK`). The skill and
  users read those lines.
- Never write over input files. Delete generated files only under `out/` or previous `- VO` outputs, and only when
  asked.
- When a run makes a new decision (cover frame, lexicon entry, script fix), report it to the user.

## Gotchas (learned the hard way)

- **Python 3.12, not the system 3.14**: spaCy (pulled in by misaki/Kokoro) doesn't build on 3.14.
- **The `uv` on PATH is SafeDep PMG's wrapper**, not real uv. `start.sh` uses `python -m venv` and the venv's pip on
  purpose.
- **No usable GPU** (Intel UHD integrated). torch is pinned to `+cpu`, which is fast enough for an 82M model.
- **Kokoro pads every sentence** with about 0.35s of silence before and about 0.5s after. `trim()` strips it and
  `SENTENCE_GAP_S` puts back a fixed pause. Without that, sentence breaks become about 1.1s of dead air.
- **Kokoro's `speed` isn't linear** in output length, so `render_clip` re-measures up to 3 times.
- **Variable-frame-rate recordings** drift when the cover is joined on. Whenever we re-encode, we force a constant
  rate with `fps=`.
- **Social previews come from the first frames**, not from embedded cover art. That's why the cover is both
  prepended as intro frames and embedded as `attached_pic`.
- **Check that the script matches the video.** A script for a different video once went through every timing
  check. Compare frames with the script content before generating.
