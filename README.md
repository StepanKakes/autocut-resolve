# AutoCut for DaVinci Resolve

Self-hosted alternative to autocut.com, built on the DaVinci Resolve scripting
API. Czech-first, runs entirely on your Mac (no cloud, no API keys), works in
the **free** version of DaVinci Resolve.

What it does, all driven by a Tkinter panel inside Resolve:

- **Auto cut silences** — energy-based silence detection, ripple-cuts them out
- **Auto captions** — local whisper.cpp transcription with custom word-count,
  punctuation and capitalisation; mapped onto the cut timeline so subtitles stay
  in sync without re-rendering
- **Filler-word removal** — Czech filler dictionary (`ehm`, `prostě`, `jako`…)
  plus your own words; everything live-editable in the transcript
- **Best-take selection** — detect groups of repeated attempts at the same
  sentence and pick which one to keep
- **Interactive transcript editor** — click any word to remove/restore it,
  changes reflect on the timeline in seconds with "Živě" mode on

## Requirements

- macOS (Apple Silicon recommended)
- [DaVinci Resolve](https://www.blackmagicdesign.com/products/davinciresolve)
  18+ (free version is fine)
- [Homebrew](https://brew.sh) (the installer uses it for ffmpeg + whisper-cpp)
- [Python 3.12 from python.org](https://www.python.org/downloads/macos/) —
  Resolve only shows `.py` scripts when this framework is present
- ~2 GB free disk space (1.5 GB whisper model + tooling)

## Install

```bash
git clone https://github.com/StepanKakes/autocut-resolve.git
cd autocut-resolve
./install.sh
```

The installer will:

1. Check / install ffmpeg and whisper-cpp via Homebrew.
2. Verify python.org Python 3.12 is present (or download the .pkg for you to
   install — Resolve needs the framework so `.py` scripts appear in the menu).
3. Download the `ggml-large-v3-turbo` whisper model (~1.5 GB) into
   `~/.autocut/models/`.
4. Copy AutoCut into `~/.autocut/app/` and write a launcher into
   `~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/AutoCut Panel.py`.

Then:

1. Quit DaVinci Resolve completely (Cmd+Q) and reopen it.
2. **Workspace → Scripts → Utility → "AutoCut Panel"**.

### One-command install with Claude Code

If you have [Claude Code](https://claude.com/claude-code) installed, just clone
the repo, `cd` in, run `claude`, and ask it to install AutoCut. The included
`CLAUDE.md` tells Claude how to do it.

## Usage

1. Open the timeline you want to clean up (the **original**, uncut one — the
   transcript editor reads its clips on V1).
2. **Workspace → Scripts → Utility → AutoCut Panel**.
3. Pick the language, tick **Vyříznout ticho**, choose filler groups, set the
   repeat-threshold if you record multiple takes.
4. **1. Analyzovat** — transcribes (Czech ~1× realtime on M-series) and shows
   the transcript with proposed cuts struck through.
5. Click words to keep/cut. For repeated takes, open
   **🎬 Vybrat nejlepší pokus ze skupin** and pick which attempt stays.
6. **2. Aplikovat střih** — creates a new timeline `<name> - AutoCut` with the
   cuts applied. Tick **Titulky** to drop a Czech subtitle track in one go
   (reuses the analysis transcript, no second whisper pass).

**Živě** rebuilds the cut timeline whenever you click a word — Descript-style
editing.

## Architecture, briefly

| File | Responsibility |
|---|---|
| `core/ui.py` | Tkinter panel (in-process inside Resolve, so it works in free DR) |
| `core/engine.py` | `analyze` → transcribes + tags cuts; `apply` → rebuilds timeline; caption builder reuses the transcript |
| `core/transcribe.py` | whisper.cpp wrapper, cancellable subprocess |
| `core/silence.py` | ffmpeg `silencedetect` wrapper |
| `core/fillers.py` | Czech filler dictionary + matching |
| `core/repeats.py` | take-group detection via `difflib` similarity |
| `core/captions.py` | SRT generation, formatting (word count / punctuation / case), subtitle import |
| `core/clips.py` | timeline-clip reading + new-timeline builder; auto-calibrates source fps from ffprobe |

The cut strategy is to **build a new timeline** from the surviving source
ranges via `MediaPool.AppendToTimeline`, which is the cross-version-reliable
way to do ripple cuts through the API. Your original timeline is never touched.

## Known limits / things to know

- Free DaVinci Resolve **blocks the external scripting API and Fusion
  UIManager**, so the panel runs *inside* Resolve as a Tkinter window. That's
  why it looks like a system dialog, not a fancy floating panel.
- Source frame rate is read from the media via `ffprobe` (Resolve's clip
  property can lie when conforming), so cuts line up exactly on the source
  frame even when timeline fps ≠ source fps (e.g. 23.976 source on 25 timeline).
- Caption generation operates from the analysis transcript whenever possible
  (no extra whisper pass) and excludes words you marked as cut.

## License

MIT. Use it, fork it, share it — credit appreciated.
