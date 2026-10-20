---
name: voiceover-video
description: Add a narrated voice-over (local Kokoro TTS) and a share-preview cover image to a video, end to end. Use when the user wants to add voice-over, narration or TTS to a video or demo/screencast, turn a timed script into narration on a video, pick or add a cover/thumbnail frame for Slack or social previews, draft a voice-over script for a video, or redo an existing voice-over with another voice. Works on any combination of video, optional script and optional cover image.
---

# Voice-over video

You run the whole pipeline: check the setup, look at the video, check (or write) the script, pick a cover,
generate, then check the result. The user gives you paths and makes decisions; they shouldn't need to run anything.

Everything goes through `./start.sh` in the project root. It sets up the venv the first time, then runs
`make_vo.py` with whatever arguments you pass. Run `./start.sh <video> --help` for all options.

## Inputs

| Input | Required | If missing |
|---|---|---|
| Video | yes | Ask for it. |
| Script | no | Draft one from the video (step 3b) and get the user's OK before generating. |
| Cover image | no | Pick a frame from the video (step 4). |
| Voice | no | `am_adam`. Others: `am_michael`, `bm_george` (British), `af_heart`, `bf_emma`. |
| Output | no | `<video name> - VO.mp4` next to the input. Never overwrite the input. |

Expand `~` and quote paths, because file names often have spaces. If the user names a file loosely ("the png in
Downloads"), `ls` the folder and confirm which file you mean when there's more than one candidate.

## Workflow

### 1. Check the setup

Run `./check.sh`. A `FAIL` means something required is missing. Stop and tell the user exactly what to install;
don't install system packages yourself. `warn` lines are fine, because `start.sh` handles them. The first
`start.sh` run downloads about 1 GB, so mention it before kicking it off.

### 2. Look at the video

```bash
./start.sh "<video>" --scenes
```

This prints the duration, resolution, whether there's an audio track, the scene-change times, and a script
skeleton with one clip per scene. If it finds no scene changes on a video that obviously has some, lower
`--scene-threshold` (e.g. `0.15`).

### 3a. Script provided: check it

1. Read the script. It must use the format under "Script format" below.
2. `./start.sh "<video>" "<script>" --check` compares every clip with the scene changes, estimates its length at
   a natural pace, and prints Kokoro's pronunciation of each clip.
3. **Check that the script matches what's on screen.** Run `--cover-candidates` (step 4) and look at the contact
   sheet. Compare what the frames show (product, topic, any burned-in captions) with what the script says. A
   script written for a different video is easy to miss and ruins the output. If it doesn't match, stop and ask.
   Don't generate.
4. Act on what `--check` reports:
   - `ERROR` (clip runs into the next, empty text): must be fixed.
   - `WARN` (start is off a scene change, too many words for the slot): propose a fix.
   - `note`: informational only.
   Never silently edit the user's script. Show the proposed change, or write a corrected copy next to it
   (`<name>.fixed.txt`) and say so.
5. Pronunciation: look for acronyms, CLI names, file names and version numbers that came out wrong (an acronym read
   as a word, `.env` read as "env"). Fix them with a lexicon file, not by editing the script: write
   `<video name>.lexicon.txt` in `out/` with lines like `kubectl = cube control`, then pass `--lexicon <file>`
   and re-run `--check` to confirm.

### 3b. No script: draft one

1. Use the `--scenes` skeleton for timing and the `--cover-candidates` contact sheet (plus any extra frames you
   need, via `ffmpeg -ss <t> -i "<video>" -frames:v 1 <scratch>.png`) for content. Burned-in captions are the best
   guide to what each scene says.
2. Write it in the script format, in the voice the user asked for (default: calm, plain, factual). Keep each clip
   within its word budget (about 2.4 words per second of `max`). Leave the first ~0.5s and the last ~0.5s of a
   scene quiet when you can.
3. Save it as `<video name>.vo.txt` next to the video, show it to the user, and **wait for approval** before
   generating.

### 4. Cover image

If the user gave one, use `--cover <image>`. It gets letterboxed to the video's frame size, never cropped.

Otherwise run:

```bash
./start.sh "<video>" --cover-candidates
```

It saves full-resolution frames from the middle and end of each scene, plus a `contact-sheet.jpg` (3 per row, same
order as the file list), in `out/<video name>/cover-candidates/`. Read the contact sheet and pick a frame by these
criteria, roughly in order:

1. **Says what the video is about at thumbnail size**: a title card, closing card or summary screen with the
   product name and a headline beats a busy terminal.
2. **Settled**: not mid-transition, faded, blurred or half-drawn. The "end of scene" frames are usually the settled ones.
3. **Readable when small**: large text and clear contrast; avoid frames dominated by tiny text.
4. **Not blank**: skip the fade-in or fade-out frames at the very start and end.

Open the chosen full-resolution frame to confirm it, then tell the user which one you picked (its timestamp) and
why in one line. Use it with `--cover-at <seconds>`, or `--cover <file>` for the saved PNG. The default intro is
1.5s (`--cover-duration`); use `0` to embed it as a thumbnail only, without adding intro frames.

### 5. Generate

```bash
./start.sh "<video>" "<script>" --cover <image> --strict [--voice ...] [--lexicon ...] [-o "<output>"]
```

`--strict` stops before building the video if any clip is still over its max at 1.3× speed, or overlaps the next
clip. If that happens, propose shorter wording for those clips (or a bigger `max` if the scene allows it), get the
user's OK, and re-run.

To compare voices, give each run its own `-o "<name> - VO (<voice>).mp4"` and
`--clips-dir "out/<name> (<voice>)"` so they don't overwrite each other.

### 6. Check the result

Don't report success without checking:

```bash
F="<output>"
ffprobe -v error -show_entries format=duration:stream=index,codec_name:stream_disposition=attached_pic -of compact "$F"
ffmpeg -hide_banner -i "$F" -map 0:a -af ebur128 -f null - 2>&1 | grep -A1 'Integrated loudness'
```

- Streams: h264 video and aac audio, plus an mjpeg stream with `attached_pic=1` when there's a cover.
- Duration: cover intro + max(video length, last clip end + 0.5s tail).
- Loudness: about -16 LUFS (anything from -15 to -18 is fine).
- Frames: grab one at 0.5s (it should be the cover) and one just after the intro (it should be the video), and look
  at them.

You can't listen to the audio, so say so and let the user judge the voice.

### 7. Report

Give the output path, then a table per clip: length, max and speed (from the generate output). Then list anything
you changed or chose (cover frame and why, lexicon entries, script fixes), and any warning still open. Offer a voice
comparison or tweaks. Before deleting earlier outputs, `ls` them and confirm with the user. Only ever delete files
this workflow generated (the `out/` folder, previous `- VO` videos), never the inputs.

## Script format

```text
# Lines starting with # are comments.
[01-opening.mp3 | start 0:00 | max 6.5s]
Your coding agent runs shell commands on your machine. Every one of them can reach the internet.

[02-observe.mp3 | start 0:07 | max 8s]
Run it under pmg sandbox. PMG records every destination the agent connects to.
```

- `start` is the time in the **original** video (`M:SS` or `M:SS.s`). The cover intro is added automatically.
- `max` is the most time the clip may take. `start + max` must not reach the next clip's start.
- The text can wrap over several lines. Sentences are split on `. ! ?`, with a fixed 0.3s pause between them.
- Kokoro reads about 2.4 words per second at speed 1.0. A slot that's too tight gets sped up, up to 1.3×.
