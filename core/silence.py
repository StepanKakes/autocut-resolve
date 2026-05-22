"""Silence detection via ffmpeg's silencedetect filter.

No machine learning needed here -- this is pure audio-energy analysis, which is
fast and reliable for the "auto cut silences" feature. Returns silent intervals
in absolute seconds relative to the start of the media file.
"""

import os
import re
import shutil
import subprocess

_SILENCE_RE = re.compile(r"silence_(start|end):\s*([\-0-9.]+)")

# Resolve launches scripts with a minimal PATH that omits Homebrew, so we look
# in the usual install locations too.
_FFMPEG_CANDIDATES = [
    "/opt/homebrew/bin/ffmpeg",  # Apple Silicon Homebrew
    "/usr/local/bin/ffmpeg",     # Intel Homebrew
    "/opt/local/bin/ffmpeg",     # MacPorts
]


def find_ffmpeg():
    """Return a usable ffmpeg path, or None. Checks PATH then common locations."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    for candidate in _FFMPEG_CANDIDATES:
        if os.path.exists(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def ffmpeg_available():
    return find_ffmpeg() is not None


def detect_silences(media_path, noise_db=-30, min_silence_dur=0.5):
    """Run ffmpeg silencedetect over the whole file.

    Args:
        media_path: path to the source media file on disk.
        noise_db: anything quieter than this (dBFS) counts as silence.
        min_silence_dur: minimum length (s) for a gap to be treated as silence.

    Returns:
        List of (start_s, end_s) tuples in absolute seconds.
    """
    ffmpeg = find_ffmpeg() or "ffmpeg"
    cmd = [
        ffmpeg, "-hide_banner", "-nostats",
        "-vn",                    # ignore video: we only analyse audio (much faster)
        "-i", media_path,
        "-af", f"silencedetect=noise={noise_db}dB:d={min_silence_dur}",
        "-f", "null", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    # silencedetect writes to stderr regardless of success.
    text = proc.stderr or ""

    silences = []
    pending_start = None
    for kind, value in _SILENCE_RE.findall(text):
        value = float(value)
        if kind == "start":
            pending_start = value
        elif pending_start is not None:  # "end"
            silences.append((pending_start, value))
            pending_start = None
    # An unterminated silence_start means silence runs to EOF; ignore (handled by
    # clip end clamping in the caller).
    return silences
