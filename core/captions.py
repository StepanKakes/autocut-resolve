"""Auto captions WITHOUT rendering (works in free DaVinci Resolve, any resolution).

Instead of rendering the timeline's audio, we transcribe each clip's *source*
file once and remap word timings onto their positions in the timeline. This
also handles a silence/filler-cut timeline correctly: words that fell into
removed regions simply don't map onto any clip, and the rest shift into place.
"""

import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from resolve_connect import get_context     # noqa: E402
from transcribe import transcribe_media      # noqa: E402
from srt import write_srt                     # noqa: E402
from clips import read_v1_clips               # noqa: E402

SETTINGS = {
    "language": "cs",
    "max_len": 42,        # max characters per subtitle line
    "max_words": 0,       # max words per subtitle (0 = no word limit)
    "max_gap": 0.7,       # start a new caption after a silence gap this long (s)
    "keep_punctuation": True,
    "case": "asis",       # asis | upper | lower | sentence
    "add_subtitle_track": True,
}

_PUNCT_RE = re.compile(r"[.,!?;:…\"»«„“”]+")


def _format_text(text, keep_punctuation, case):
    t = text.strip()
    if not keep_punctuation:
        t = re.sub(r"\s+", " ", _PUNCT_RE.sub("", t)).strip()
    if case == "upper":
        t = t.upper()
    elif case == "lower":
        t = t.lower()
    elif case == "sentence" and t:
        t = t[0].upper() + t[1:]
    return t


def _remap_words_to_timeline(clips, timeline_fps, tl_start_frame, language, log):
    """Transcribe each source once, return words with timeline-relative times."""
    cache = {}
    words_tl = []
    for clip in clips:
        path = clip["path"]
        if path not in cache:
            log(f"  Transcribing {os.path.basename(path)}...")
            cache[path] = transcribe_media(path, language=language, max_len=1, log=log)

        src_fps = clip["src_fps"]
        src_start_s = clip["src_start_frame"] / src_fps
        src_end_s = (clip["src_end_frame"] + 1) / src_fps
        rec_offset_s = (clip["rec_start_frame"] - tl_start_frame) / timeline_fps

        for w in cache[path]:
            if w["end"] <= src_start_s or w["start"] >= src_end_s:
                continue  # word lies outside the part of the source this clip uses
            ws = max(w["start"], src_start_s)
            we = min(w["end"], src_end_s)
            words_tl.append({
                "start": rec_offset_s + (ws - src_start_s),
                "end": rec_offset_s + (we - src_start_s),
                "text": w["text"],
            })
    words_tl.sort(key=lambda x: x["start"])
    return words_tl


def _group_words(words, max_len, max_gap, max_words=0):
    """Group consecutive words into caption segments by word count, line length
    and time gaps."""
    segments = []
    cur = None
    count = 0
    for w in words:
        if cur is None:
            cur = {"start": w["start"], "end": w["end"], "text": w["text"]}
            count = 1
            continue
        gap = w["start"] - cur["end"]
        too_long = len(cur["text"]) + 1 + len(w["text"]) > max_len
        too_many = max_words and count >= max_words
        if gap > max_gap or too_long or too_many:
            segments.append(cur)
            cur = {"start": w["start"], "end": w["end"], "text": w["text"]}
            count = 1
        else:
            cur["text"] += " " + w["text"]
            cur["end"] = w["end"]
            count += 1
    if cur:
        segments.append(cur)
    return segments


def run(settings=None, log=print, resolve_app=None):
    cfg = dict(SETTINGS)
    if settings:
        cfg.update(settings)

    resolve, project, media_pool, timeline = get_context(resolve_app)
    timeline_fps = float(timeline.GetSetting("timelineFrameRate") or
                         project.GetSetting("timelineFrameRate") or 25)
    tl_start = int(timeline.GetStartFrame())
    log(f"Timeline: {timeline.GetName()} @ {timeline_fps} fps")

    clips = read_v1_clips(timeline, timeline_fps, log=log)
    if not clips:
        raise RuntimeError("No usable clips on video track 1.")

    words = _remap_words_to_timeline(clips, timeline_fps, tl_start, cfg["language"], log)
    if not words:
        raise RuntimeError("Transcription returned no words.")
    segments = _group_words(words, cfg["max_len"], cfg["max_gap"], cfg["max_words"])
    for s in segments:
        s["text"] = _format_text(s["text"], cfg["keep_punctuation"], cfg["case"])
    log(f"Built {len(segments)} caption(s) from {len(words)} words.")

    srt_path = os.path.join(tempfile.mkdtemp(prefix="autocut_"), "captions.srt")
    write_srt(segments, srt_path)

    if cfg["add_subtitle_track"]:
        timeline.AddTrack("subtitle")

    imported = media_pool.ImportMedia([srt_path]) or []
    if not imported:
        raise RuntimeError(f"ImportMedia failed. SRT saved at {srt_path}; import manually.")
    clip_info = [{"mediaPoolItem": item, "recordFrame": tl_start} for item in imported]
    appended = media_pool.AppendToTimeline(clip_info)
    n = len(appended) if isinstance(appended, list) else len(segments)
    log(f"Done. Added {n} subtitle(s). SRT: {srt_path}")
    return srt_path


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        print(f"AutoCut captions error: {exc}")
        raise
