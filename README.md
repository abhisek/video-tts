# video-tts

Give Claude a video and a script. You get back the same video with a natural-sounding voice-over, and a cover image
that shows up as the preview when you share it on Slack, X or LinkedIn.

Everything runs locally: [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) for speech and ffmpeg for video.
There are no API keys, no credits and no GPU needed.

## Setup

You need [Claude Code](https://claude.com/claude-code), [mise](https://mise.jdx.dev) and ffmpeg. Then open Claude
Code in this folder:

```bash
cd video-tts && claude
```

> Check if this machine is ready to make voice-over videos.

Claude runs `./check.sh` and tells you what's missing, if anything. You don't need to run `mise install` or
`pip install` yourself: the first video takes a few extra minutes while Python 3.12 (via mise), PyTorch (CPU) and
the voice model download (about 1 GB, once).

## Make a video

> Add a voice-over to ~/Downloads/demo.mp4 using the script ~/Downloads/demo-vo.txt and ~/Downloads/thumb.png as
> the cover.

Claude then:

1. checks the setup and looks at the video (length, scene changes, existing audio);
2. checks the script's timing against the scene changes, and checks that it actually matches what's on screen;
3. checks pronunciation of acronyms and CLI names and adds fixes if needed;
4. generates the narration, fitting each line into its time slot;
5. adds the cover as the first 1.5 seconds and embeds it as the thumbnail;
6. checks the output (streams, duration, loudness, first frames) and reports a per-line summary.

The result is saved next to your video as `demo - VO.mp4`.

### More prompts

**No cover image?** Claude picks a frame from the video and tells you which and why.

> Add a voice-over to ~/Downloads/demo.mp4 with ~/Downloads/demo-vo.txt. Pick a good cover frame from the video.

**No script?** Claude drafts one from what's on screen and waits for your OK.

> Write a voice-over script for ~/Downloads/demo.mp4 based on what's on screen, calm and factual, then generate it
> once I approve.

**Compare voices:**

> Make versions of that video with am_michael and bm_george so I can compare.

**Fix a word:**

> It says "pumg" instead of P-M-G. Fix it and regenerate.

**Housekeeping:**

> I'm keeping the bm_george version. Delete the other generated videos and clips.

## Script format

```text
# Comments start with #.
[01-opening.mp3 | start 0:00 | max 6.5s]
Your coding agent runs shell commands on your machine. Every one of them can reach the internet.

[02-observe.mp3 | start 0:07 | max 8s]
Run it under pmg sandbox. PMG records every destination the agent connects to.
```

- `start` is when the line begins in the original video. `max` is the longest it may run.
- Plan on about 2.4 words per second of `max`. Claude will warn you if a line is too long for its slot.

## Voices

`am_adam` (default), `am_michael`, `bm_george` (British), `af_heart`, `bf_emma`. See the
[full list](https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md). American voices start with `a`,
British with `b`.

## Under the hood

Claude follows the `voiceover-video` skill in `.claude/skills/`. Project notes for agents are in `AGENTS.md`. You
can run the same steps yourself:

```bash
./check.sh
./start.sh VIDEO --scenes                    # scene changes + script skeleton
./start.sh VIDEO --cover-candidates          # cover frame candidates + contact sheet
./start.sh VIDEO SCRIPT --check              # timing and pronunciation check
./start.sh VIDEO SCRIPT --cover IMG --strict # build the video
./start.sh VIDEO --help                      # every option
```
