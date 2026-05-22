# AutoCut for DaVinci Resolve

A self-hosted alternative to autocut.com, built on the DaVinci Resolve scripting
API. Czech-first.

## Planned features
- [x] **Auto cut silences** — remove silent gaps (ffmpeg energy analysis, no ML)
- [ ] **Auto captions** — Czech subtitles from local faster-whisper
- [ ] **Filler-word removal** — cut "ehm", "jakože", "prostě"… from transcript
- [ ] **Auto cut repeat** — detect repeated takes, keep the best one

## Architecture
- **`core/`** — Python "brains". Connects to Resolve, analyses audio, rebuilds
  the timeline. Runs standalone so it's easy to debug.
- **Workflow Integration panel** (later) — an Electron/JS UI inside Resolve that
  just collects settings and runs the Python worker.

Cutting strategy: Resolve's API has no reliable cross-version split+ripple-delete,
so we **build a new timeline** containing only the segments to keep
(`MediaPool.AppendToTimeline`). The original timeline is left untouched.

### Transcription (later milestones)
Local **faster-whisper** in a dedicated venv (system Python is 3.9). Gives
word-level timestamps that drive captions, filler removal, and repeat detection.

## How to run (current MVP: cut silences)
Requirements: DaVinci Resolve 18+ running, `ffmpeg` on PATH.

1. Open the project and the timeline you want to cut.
2. In Resolve: **Workspace → Scripts → Utility → "AutoCut - Cut Silences"**.
3. A new timeline `"<name> - AutoCut"` appears with silences removed.

Tune behaviour in `core/autocut_silences.py` → `SETTINGS`
(`noise_db`, `min_silence_dur`, `pad`, …) until the panel UI exists.

The launcher lives at:
`~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/AutoCut - Cut Silences.py`
and just points back to `core/` in this repo.

## Layout
```
core/
  resolve_connect.py    # connect to a running Resolve
  silence.py            # ffmpeg silencedetect -> silent intervals
  intervals.py          # pure math: silences -> keep segments (unit-testable)
  autocut_silences.py   # main entry: read timeline, cut, rebuild
```
