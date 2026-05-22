"""Transcription via whisper.cpp (the `whisper-cli` binary).

Reuses the ggml model already on disk (e.g. Vowen's large-v3-turbo) so nothing
needs downloading. Produces segments with start/end times in seconds, plus
word-level timing when requested (needed later for filler-word removal and
repeat detection).
"""

import json
import os
import shutil
import subprocess
import tempfile

from silence import find_ffmpeg

# whisper-cli ships with a few apps; check PATH then known locations.
_WHISPER_CANDIDATES = [
    "/Applications/Vowen.app/Contents/Resources/bin/whisper-cli",
    "/opt/homebrew/bin/whisper-cli",
    "/usr/local/bin/whisper-cli",
]

# ggml models already on disk.
_MODEL_CANDIDATES = [
    os.path.expanduser("~/Library/Application Support/vowen/models/ggml-large-v3-turbo.bin"),
]


def find_whisper_cli():
    found = shutil.which("whisper-cli")
    if found:
        return found
    for c in _WHISPER_CANDIDATES:
        if os.path.exists(c) and os.access(c, os.X_OK):
            return c
    return None


def find_model():
    for c in _MODEL_CANDIDATES:
        if os.path.exists(c):
            return c
    return None


def extract_audio(media_path, out_wav, start_s=None, dur_s=None):
    """Extract mono 16 kHz WAV (what whisper.cpp expects) via ffmpeg."""
    ffmpeg = find_ffmpeg() or "ffmpeg"
    cmd = [ffmpeg, "-hide_banner", "-nostats", "-y"]
    if start_s is not None:
        cmd += ["-ss", str(start_s)]
    if dur_s is not None:
        cmd += ["-t", str(dur_s)]
    cmd += ["-i", media_path, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", out_wav]
    subprocess.run(cmd, capture_output=True, text=True, check=True)
    return out_wav


def transcribe_wav(wav_path, language="cs", model=None, whisper=None, max_len=0, log=print):
    """Run whisper-cli on a WAV and return a list of segments.

    Each segment: {"start": float_s, "end": float_s, "text": str}.
    """
    whisper = whisper or find_whisper_cli()
    if not whisper:
        raise RuntimeError("whisper-cli not found (Vowen app, PATH, or Homebrew).")
    model = model or find_model()
    if not model:
        raise RuntimeError("No ggml whisper model found on disk.")

    out_base = os.path.splitext(wav_path)[0] + ".out"
    cmd = [
        whisper, "-m", model, "-l", language,
        "-oj", "-of", out_base,
        "-pp",                      # progress
    ]
    if max_len:
        cmd += ["-ml", str(max_len), "-sow"]
    cmd += [wav_path]

    log(f"Transcribing ({os.path.basename(model)}, lang={language})...")
    subprocess.run(cmd, capture_output=True, text=True, check=True)

    json_path = out_base + ".json"
    with open(json_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    segments = []
    for seg in data.get("transcription", []):
        offs = seg.get("offsets", {})
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        segments.append({
            "start": offs.get("from", 0) / 1000.0,  # ms -> s
            "end": offs.get("to", 0) / 1000.0,
            "text": text,
        })
    return segments


def transcribe_media(media_path, language="cs", start_s=None, dur_s=None,
                     max_len=0, log=print):
    """Convenience: extract audio from any media file, then transcribe."""
    with tempfile.TemporaryDirectory() as tmp:
        wav = os.path.join(tmp, "audio.wav")
        extract_audio(media_path, wav, start_s=start_s, dur_s=dur_s)
        return transcribe_wav(wav, language=language, max_len=max_len, log=log)


if __name__ == "__main__":
    import sys
    path = sys.argv[1]
    dur = float(sys.argv[2]) if len(sys.argv) > 2 else None
    segs = transcribe_media(path, dur_s=dur)
    for s in segs:
        print(f"[{s['start']:6.2f} - {s['end']:6.2f}] {s['text']}")
