# AutoCut for DaVinci Resolve — agent guide

This repo ships a Python panel that runs **inside** DaVinci Resolve (free
version compatible) to edit Czech / multilingual talking-head footage:
auto-cut silences, remove filler words, pick best take from repeated attempts,
generate captions — all from one Tkinter window.

When the user asks you to **install it for them**, run `./install.sh` from the
repo root. The script is the single source of truth for setup steps; it is
idempotent and prints what it does.

If the script bails out asking the user to install Python 3.12 from python.org,
tell them to run the `sudo installer …` command it printed (it can't run sudo
non-interactively) and then re-run `./install.sh`.

## What `install.sh` does

1. Checks macOS + DaVinci Resolve in `/Applications`.
2. Installs `ffmpeg` and `whisper-cpp` via Homebrew (requires Homebrew already
   present — point them at <https://brew.sh> if not).
3. Verifies `/Library/Frameworks/Python.framework/Versions/3.12` exists. If
   not, downloads `python-3.12.8-macos11.pkg` into `~/Downloads/` and asks the
   user to run the installer (`sudo installer -pkg … -target /`). Resolve
   ignores `.py` scripts unless this framework is present.
4. Downloads `ggml-large-v3-turbo.bin` (~1.5 GB) into `~/.autocut/models/`.
5. Copies `core/` into `~/.autocut/app/core/`.
6. Writes a launcher to
   `~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/AutoCut Panel.py`.

After install, the user must **quit DaVinci Resolve fully (Cmd+Q) and reopen
it** — Resolve only scans the Scripts menu at startup.

## How to invoke the panel

`Workspace → Scripts → Utility → AutoCut Panel`.

The panel opens as a Tk window (looks like a native macOS dialog) and lets the
user analyse the current timeline, edit the transcript, and apply the cut.

## Useful facts for debugging

- Free Resolve blocks the **external scripting API** and the **Fusion
  UIManager**. That's why the panel uses Tkinter (in-process) instead of a
  native Resolve panel. Don't suggest external-process architectures.
- Resolve launches Python with a minimal PATH; `ffmpeg`/`whisper-cli` are
  resolved from PATH **and** known Homebrew locations
  (`/opt/homebrew/bin`, `/usr/local/bin`).
- Source frame rate comes from `ffprobe` (Resolve's `GetClipProperty('FPS')`
  can lie when the clip is conformed). See `core/clips.py:probe_fps`.
- Subprocesses force `encoding="utf-8"` because Resolve's embedded Python
  defaults to ASCII.
- The launcher clears `sys.modules` for the project's modules every run, so
  code edits land without restarting Resolve.

## Repo layout

```
install.sh        # macOS installer (the agent should run this on request)
core/             # Python package that runs inside Resolve
  ui.py           # Tkinter panel
  engine.py       # analyze() and apply() pipeline + caption builder
  transcribe.py   # whisper.cpp wrapper, cancellable
  silence.py      # ffmpeg silencedetect
  fillers.py      # Czech filler vocabulary + detection
  repeats.py      # take-group detection (difflib similarity)
  captions.py     # SRT generation + import on a subtitle track
  clips.py        # timeline clip reader + new-timeline builder
  resolve_connect.py  # connect to Resolve + UI helpers
```

## Things NOT to do

- Don't try to wire AutoCut to a separate external app talking to Resolve over
  a socket — free Resolve blocks external API connections.
- Don't switch the UI to `ttk` "clam" theme; the user prefers native macOS
  checkboxes/buttons.
- Don't paginate the model download to multiple files — it's a single ~1.5 GB
  blob from Hugging Face; just `curl --progress-bar`.

## When the user wants new features

Ideas already discussed but not built:
- Sentence-embedding-based take detection (handles paraphrased re-takes).
- Burned-in styled captions (Text+ generator route).
- Saving/restoring panel settings to `~/.autocut/settings.json`.

These are nice-to-haves; build them only when asked.
