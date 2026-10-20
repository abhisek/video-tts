"""Generate voice-over clips from a timed script with Kokoro and mux them onto a video.

Script format (lines starting with # are ignored):

    [01-opening.mp3 | start 0:00 | max 6s]
    Text to speak.

Modes (default: generate clips and build the video):
    --scenes             print the video's scene changes and a script skeleton (no script needed)
    --cover-candidates   save candidate cover frames and a contact sheet (no script needed)
    --check              check script timing against scene changes and show pronunciations
"""

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

SAMPLE_RATE = 24000
SENTENCE_GAP_S = 0.3
EDGE_PAD_S = 0.05
SILENCE_THRESHOLD = 10 ** (-40 / 20)  # -40 dBFS
MAX_SPEED = 1.3
WORDS_PER_SECOND = 2.4  # rough Kokoro pace at speed 1.0; only for script-skeleton word budgets
SCENE_TOLERANCE_S = 0.5
SENTENCE_SPLIT = r"(?<=[.!?])\s+"

HEADER = re.compile(r"^\[(?P<name>[^|\]]+)\|\s*start\s+(?P<start>[\d:.]+)\s*\|\s*max\s+(?P<max>[\d.]+)s\s*\]$")


@dataclass
class Clip:
    name: str
    start: float
    max_len: float
    text: str
    path: Path | None = None
    length: float = 0.0


@dataclass
class VideoInfo:
    duration: float
    width: int
    height: int
    fps: str
    has_audio: bool


def parse_timestamp(ts: str) -> float:
    secs = 0.0
    for part in ts.split(":"):
        secs = secs * 60 + float(part)
    return secs


def fmt_ts(secs: float) -> str:
    return f"{int(secs // 60)}:{secs % 60:05.2f}"


def parse_script(path: Path) -> list[Clip]:
    clips: list[Clip] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if m := HEADER.match(line):
            clips.append(Clip(m["name"].strip(), parse_timestamp(m["start"]), float(m["max"]), ""))
        elif clips:
            clips[-1].text = f"{clips[-1].text} {line}".strip()
        elif line.startswith("["):
            raise ValueError(f"malformed clip header (want [name.mp3 | start M:SS | max Ns]): {line!r}")
        else:
            raise ValueError(f"text before first clip header: {line!r}")
    if not clips:
        raise ValueError(f"no clips found in {path}")
    return clips


def parse_lexicon(path: Path) -> list[tuple[re.Pattern, str]]:
    """Lines of `word = replacement`; matched as whole words, case-insensitively."""
    rules = []
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        word, sep, repl = line.partition("=")
        if not sep:
            raise ValueError(f"lexicon line needs 'word = replacement': {line!r}")
        rules.append((re.compile(rf"(?<!\w){re.escape(word.strip())}(?!\w)", re.IGNORECASE), repl.strip()))
    return rules


def apply_lexicon(text: str, rules: list[tuple[re.Pattern, str]]) -> str:
    for pattern, repl in rules:
        text = pattern.sub(repl, text)
    return text


# --- video analysis ---------------------------------------------------------------------------

def probe(path: Path) -> VideoInfo:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=codec_type,width,height,avg_frame_rate", "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    )
    info = json.loads(out.stdout)
    streams = info.get("streams", [])
    v = next(st for st in streams if st.get("codec_type") == "video")
    return VideoInfo(
        duration=float(info["format"]["duration"]),
        width=int(v["width"]),
        height=int(v["height"]),
        fps=v["avg_frame_rate"],
        has_audio=any(st.get("codec_type") == "audio" for st in streams),
    )


def detect_scenes(video: Path, threshold: float) -> list[float]:
    """Times where the picture changes a lot; bursts within the tolerance (e.g. fades) count once."""
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(video), "-vf", f"select='gt(scene,{threshold})',showinfo",
         "-f", "null", "-"],
        capture_output=True, text=True,
    )
    times = [float(t) for t in re.findall(r"pts_time:([\d.]+)", out.stderr)]
    scenes: list[float] = []
    for t in times:
        if t > SCENE_TOLERANCE_S and (not scenes or t - scenes[-1] > SCENE_TOLERANCE_S):
            scenes.append(t)
    return scenes


def extract_frame(video: Path, at: float, dest: Path) -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{at:.3f}", "-i", str(video), "-frames:v", "1", str(dest)],
        check=True,
    )
    return dest


def contact_sheet(frames: list[Path], dest: Path, cols: int = 3) -> Path:
    """Tile frames (scaled down) into one image, left-to-right then top-to-bottom."""
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for f in frames:
        cmd += ["-i", str(f)]
    scaled = ";".join(f"[{i}:v]scale=640:-2[s{i}]" for i in range(len(frames)))
    if len(frames) == 1:
        filt = f"{scaled};[s0]null[out]"
    else:
        pos = []
        for i in range(len(frames)):
            col, row = i % cols, i // cols
            pos.append(f"{'+'.join(['w0'] * col) or '0'}_{'+'.join(['h0'] * row) or '0'}")
        inputs = "".join(f"[s{i}]" for i in range(len(frames)))
        filt = f"{scaled};{inputs}xstack=inputs={len(frames)}:layout={'|'.join(pos)}:fill=black[out]"
    subprocess.run(cmd + ["-filter_complex", filt, "-map", "[out]", "-frames:v", "1", str(dest)], check=True)
    return dest


def segments(scenes: list[float], duration: float) -> list[tuple[float, float]]:
    bounds = [0.0, *scenes, duration]
    return list(zip(bounds, bounds[1:]))


def cmd_scenes(video: Path, info: VideoInfo, threshold: float) -> None:
    scenes = detect_scenes(video, threshold)
    print(f"Video: {info.duration:.2f}s, {info.width}x{info.height}, audio: {'yes' if info.has_audio else 'no'}")
    print(f"Scene changes (threshold {threshold}): {', '.join(f'{s:.2f}' for s in scenes) or 'none'}")
    print("\nScript skeleton (one clip per scene, 0.5s breathing room before the next):\n")
    for i, (a, b) in enumerate(segments(scenes, info.duration), 1):
        max_len = max(round(b - a - SCENE_TOLERANCE_S, 1), 1.0)
        print(f"[{i:02d}-scene.mp3 | start {int(a // 60)}:{a % 60:04.1f} | max {max_len:g}s]")
        print(f"<narration for {fmt_ts(a)}-{fmt_ts(b)}, about {int(max_len * WORDS_PER_SECOND)} words max>\n")


def cmd_cover_candidates(video: Path, info: VideoInfo, threshold: float, out_dir: Path) -> None:
    """A frame from the middle and the settled end of each scene; title/closing cards are often best."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.png"):
        old.unlink()
    times = []
    for a, b in segments(detect_scenes(video, threshold), info.duration):
        times += [a + (b - a) / 2, max(b - 0.4, a)]
    times = sorted({round(t, 2) for t in times})
    if len(times) > 12:
        times = [times[round(i * (len(times) - 1) / 11)] for i in range(12)]
    frames = [extract_frame(video, t, out_dir / f"{i:02d}-at-{t:.2f}s.png") for i, t in enumerate(times, 1)]
    sheet = contact_sheet(frames, out_dir / "contact-sheet.jpg")
    print(f"{len(frames)} candidates (full resolution) in {out_dir}:")
    for f in frames:
        print(f"  {f.name}")
    print(f"Contact sheet (3 per row, same order): {sheet}")
    print("Use one with --cover <file> or --cover-at <seconds>.")


def cmd_check(video: Path, info: VideoInfo, clips: list[Clip], threshold: float,
              lexicon: list[tuple[re.Pattern, str]], voice: str, speed: float) -> int:
    """Timing vs scene changes, exact natural length (synthesized, not saved) and pronunciation per clip."""
    from kokoro import KPipeline  # slow import

    pipeline = KPipeline(lang_code=voice[0], repo_id="hexgrad/Kokoro-82M")
    scenes = detect_scenes(video, threshold)
    bounds = [0.0, *scenes]
    problems = 0
    print(f"Video: {info.duration:.2f}s; scene changes: {', '.join(f'{s:.2f}' for s in scenes) or 'none'}")
    print(f"Voice {voice} at speed {speed:g}\n")
    for i, c in enumerate(clips):
        end = c.start + c.max_len
        nxt = clips[i + 1].start if i + 1 < len(clips) else None
        text = apply_lexicon(c.text, lexicon)
        natural = len(synthesize(pipeline, text, voice, speed)) / SAMPLE_RATE if text else 0.0
        notes = []
        if not c.text:
            notes.append("ERROR: no text")
        nearest = min(bounds, key=lambda b: abs(b - c.start))
        if abs(nearest - c.start) > SCENE_TOLERANCE_S:
            notes.append(f"WARN: starts {abs(nearest - c.start):.1f}s from the nearest scene change ({nearest:.2f})")
        crossed = [s for s in scenes if c.start + SCENE_TOLERANCE_S < s < end - 0.25]
        if crossed:
            notes.append(f"note: slot runs across scene change(s) at {', '.join(f'{s:.2f}' for s in crossed)}")
        if nxt is not None and end > nxt:
            notes.append(f"ERROR: start + max ({end:.2f}) runs into the next clip ({nxt:.2f})")
        if natural > c.max_len * MAX_SPEED:
            notes.append(f"ERROR: needs {natural / c.max_len:.2f}x to fit, over the {MAX_SPEED}x cap; shorten the text")
        elif natural > c.max_len:
            notes.append(f"WARN: will be sped up to ~{natural / c.max_len:.2f}x to fit")
        if nxt is None and end > info.duration:
            notes.append(f"note: may run past the video end ({info.duration:.2f}); last frame would be held")
        problems += sum(n.startswith(("ERROR", "WARN")) for n in notes)
        print(f"{c.name}  start {c.start:.2f}  max {c.max_len:g}s  natural {natural:.2f}s")
        for n in notes:
            print(f"  {n}")
        print(f"  says: {pipeline.g2p(text)[0]}")
    print(f"\n{problems} warning(s)/error(s). Fix odd pronunciations with --lexicon.")
    return problems


# --- synthesis --------------------------------------------------------------------------------

def trim(audio: np.ndarray) -> np.ndarray:
    """Cut Kokoro's leading/trailing silence (~0.35s/~0.5s per chunk), keeping a small margin."""
    loud = np.flatnonzero(np.abs(audio) > SILENCE_THRESHOLD)
    if loud.size == 0:
        return audio
    pad = int(EDGE_PAD_S * SAMPLE_RATE)
    return audio[max(loud[0] - pad, 0): loud[-1] + pad + 1]


def synthesize(pipeline, text: str, voice: str, speed: float) -> np.ndarray:
    """Speak each sentence separately and join them with a short, fixed pause."""
    gap = np.zeros(int(SENTENCE_GAP_S * SAMPLE_RATE), dtype=np.float32)
    parts = []
    for _, _, audio in pipeline(text, voice=voice, speed=speed, split_pattern=SENTENCE_SPLIT):
        if audio is not None:
            parts.extend([trim(audio.numpy()), gap])
    if not parts:
        raise RuntimeError(f"no audio produced for: {text!r}")
    return np.concatenate(parts[:-1])


def render_clip(pipeline, clip: Clip, text: str, voice: str, base_speed: float, out_dir: Path) -> None:
    speed = base_speed
    audio = synthesize(pipeline, text, voice, speed)
    length = len(audio) / SAMPLE_RATE
    # Speed up just enough to fit. Kokoro's speed isn't exactly linear in length, so re-check a few times.
    for _ in range(3):
        if length <= clip.max_len or speed >= MAX_SPEED:
            break
        speed = min(speed * length / clip.max_len * 1.02, MAX_SPEED)
        audio = synthesize(pipeline, text, voice, speed)
        length = len(audio) / SAMPLE_RATE

    wav = out_dir / (Path(clip.name).stem + ".wav")
    sf.write(wav, audio, SAMPLE_RATE)
    clip.path = out_dir / clip.name
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav), "-codec:a", "libmp3lame", "-q:a", "2", str(clip.path)],
        check=True,
    )
    wav.unlink()
    clip.length = length

    status = "OK" if length <= clip.max_len else f"OVER by {length - clip.max_len:.2f}s"
    print(f"  {clip.name}: {length:.2f}s / max {clip.max_len:g}s at speed {speed:.2f}  {status}")


# --- muxing -----------------------------------------------------------------------------------

def fit(width: int, height: int) -> str:
    """Scale to fit the frame, letterboxing instead of cropping."""
    return (f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1")


def mux(video: Path, info: VideoInfo, clips: list[Clip], output: Path, tail_pad: float, bg_volume: float,
        cover: Path | None, cover_dur: float) -> None:
    intro = cover_dur if cover else 0.0
    vo_end = max(c.start + c.length for c in clips) + tail_pad
    body = max(info.duration, vo_end)
    extend = body - info.duration
    total = intro + body

    # Inputs: 0 = video, 1..N = clips, then the cover image (intro frames, embedded thumbnail).
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video)]
    for c in clips:
        cmd += ["-i", str(c.path)]
    next_idx = len(clips) + 1
    if intro > 0:
        intro_idx, next_idx = next_idx, next_idx + 1
        cmd += ["-loop", "1", "-framerate", info.fps, "-t", f"{intro:.3f}", "-i", str(cover)]
    if cover:
        thumb_idx = next_idx
        cmd += ["-i", str(cover)]

    # Script start times are relative to the original video, so shift them past the intro.
    filters = [f"[{i}:a]adelay={int((intro + c.start) * 1000)}:all=1[a{i}]" for i, c in enumerate(clips, 1)]
    mixed = "".join(f"[a{i}]" for i in range(1, len(clips) + 1))
    filters.append(
        f"{mixed}amix=inputs={len(clips)}:normalize=0:duration=longest,"
        f"loudnorm=I=-16:TP=-1.5:LRA=11,apad=whole_dur={total:.3f}[vo]"
    )
    if info.has_audio and bg_volume > 0:
        # Keep the original soundtrack underneath the narration.
        print(f"Video has its own audio: mixing it under the voice-over at {bg_volume:.0%}")
        filters.append(f"[0:a]adelay={int(intro * 1000)}:all=1,volume={bg_volume},apad=whole_dur={total:.3f}[bg]")
        filters.append("[vo][bg]amix=inputs=2:normalize=0:duration=first[aout]")
    else:
        if info.has_audio:
            print("Video has its own audio: replacing it (--bg-volume 0)")
        filters.append("[vo]anull[aout]")

    reencode = intro > 0 or extend > 0
    if reencode:
        # fps= forces a constant frame rate; screen recordings are often variable, which drifts after concat.
        main = f"[0:v]fps={info.fps},"
        if extend > 0:
            # Hold the last frame so the final line isn't cut off.
            print(f"Video is {info.duration:.2f}s, voice-over needs {body:.2f}s: holding last frame for {extend:.2f}s")
            main += f"tpad=stop_mode=clone:stop_duration={extend:.3f},"
        if intro > 0:
            print(f"Prepending {intro:.2f}s cover from {cover.name}; voice-over shifted by the same amount")
            filters.append(f"[{intro_idx}:v]{fit(info.width, info.height)},format=yuv420p[intro]")
            filters.append(f"{main}setsar=1,format=yuv420p[main]")
            filters.append("[intro][main]concat=n=2:v=1:a=0[vout]")
        else:
            filters.append(f"{main}format=yuv420p[vout]")
        vmap, vcodec = "[vout]", ["-c:v:0", "libx264", "-preset", "medium", "-crf", "18"]
    else:
        vmap, vcodec = "0:v", ["-c:v:0", "copy"]

    thumb = []
    if cover:
        # Embedded cover art: shown by file browsers and some players, ignored by most social sites.
        filters.append(f"[{thumb_idx}:v]{fit(info.width, info.height)},format=yuvj420p[thumb]")
        thumb = ["-map", "[thumb]", "-c:v:1", "mjpeg", "-disposition:v:1", "attached_pic"]

    cmd += [
        "-filter_complex", ";".join(filters),
        "-map", vmap, "-map", "[aout]", *thumb,
        *vcodec, "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-t", f"{total:.3f}", "-movflags", "+faststart", str(output),
    ]
    subprocess.run(cmd, check=True)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("video", type=Path, help="input video")
    p.add_argument("script", type=Path, nargs="?", help="timed voice-over script (not needed for --scenes/--cover-candidates)")
    p.add_argument("-o", "--output", type=Path, help="output video (default: '<video> - VO.mp4' next to the input)")
    p.add_argument("--clips-dir", type=Path, help="where per-clip MP3s are written (default: out/<video name>/ next to this script)")
    p.add_argument("--voice", default="am_adam", help="Kokoro voice, e.g. am_adam, am_michael, af_heart, bm_george")
    p.add_argument("--speed", type=float, default=1.0, help="base speaking speed")
    p.add_argument("--tail-pad", type=float, default=0.5, help="seconds of silence after the last line")
    p.add_argument("--bg-volume", type=float, default=0.3, help="volume of the video's own audio, if any (0 = replace)")
    p.add_argument("--lexicon", type=Path, help="pronunciation fixes, one 'word = replacement' per line")
    cover = p.add_mutually_exclusive_group()
    cover.add_argument("--cover", type=Path, help="image shown as the opening frames and embedded as the thumbnail")
    cover.add_argument("--cover-at", type=float, metavar="SECONDS", help="use the video's frame at this time as the cover")
    p.add_argument("--cover-duration", type=float, default=1.5,
                   help="seconds the cover is shown before the video (0 = embed as thumbnail only)")
    p.add_argument("--scene-threshold", type=float, default=0.25, help="scene-change sensitivity, 0-1 (lower = more)")
    p.add_argument("--strict", action="store_true", help="fail instead of warning when a clip overruns or overlaps")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--scenes", action="store_true", help="print scene changes and a script skeleton, then exit")
    mode.add_argument("--cover-candidates", action="store_true", help="save candidate cover frames, then exit")
    mode.add_argument("--check", action="store_true", help="check script timing and pronunciation, then exit")
    mode.add_argument("--clips-only", action="store_true", help="generate clips but don't build the video")
    args = p.parse_args()

    for f in (args.video, args.script, args.cover, args.lexicon):
        if f and not f.exists():
            p.error(f"not found: {f}")

    args.output = args.output or args.video.with_name(f"{args.video.stem} - VO.mp4")
    args.clips_dir = args.clips_dir or Path(__file__).resolve().parent / "out" / args.video.stem
    if args.output.resolve() == args.video.resolve():
        p.error("output would overwrite the input video")

    info = probe(args.video)
    if args.scenes:
        cmd_scenes(args.video, info, args.scene_threshold)
        return 0
    if args.cover_candidates:
        cmd_cover_candidates(args.video, info, args.scene_threshold, args.clips_dir / "cover-candidates")
        return 0
    if not args.script:
        p.error("a script is required (except with --scenes or --cover-candidates)")

    clips = parse_script(args.script)
    lexicon = parse_lexicon(args.lexicon) if args.lexicon else []
    if args.check:
        problems = cmd_check(args.video, info, clips, args.scene_threshold, lexicon, args.voice, args.speed)
        return 2 if args.strict and problems else 0

    args.clips_dir.mkdir(parents=True, exist_ok=True)
    if args.cover_at is not None:
        if not 0 <= args.cover_at < info.duration:
            p.error(f"--cover-at must be within the video (0-{info.duration:.2f}s)")
        args.cover = extract_frame(args.video, args.cover_at, args.clips_dir / f"cover-at-{args.cover_at:.2f}s.png")

    from kokoro import KPipeline  # slow import; keep --help fast

    # Voice prefix picks the accent: a* = American, b* = British.
    pipeline = KPipeline(lang_code=args.voice[0], repo_id="hexgrad/Kokoro-82M")

    print(f"Generating {len(clips)} clips with voice {args.voice}")
    for clip in clips:
        render_clip(pipeline, clip, apply_lexicon(clip.text, lexicon), args.voice, args.speed, args.clips_dir)

    failed = [c for c in clips if c.length > c.max_len]
    for cur, nxt in zip(clips, clips[1:]):
        if cur.start + cur.length > nxt.start:
            print(f"  WARNING: {cur.name} overlaps {nxt.name} by {cur.start + cur.length - nxt.start:.2f}s")
            failed.append(cur)
    if args.strict and failed:
        print("Stopping (--strict): shorten the text or raise max for the clips above.", file=sys.stderr)
        return 2

    if args.clips_only:
        print(f"Clips written to {args.clips_dir.resolve()}")
        return 0

    mux(args.video, info, clips, args.output, args.tail_pad, args.bg_volume, args.cover, args.cover_duration)
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
