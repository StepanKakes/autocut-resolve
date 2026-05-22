"""Unified AutoCut pipeline: cut silences and/or fillers in one timeline
rebuild, then optionally generate captions on the result.

Driven by a single settings dict so the UI just collects values and calls run().
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import captions as captions_mod                      # noqa: E402
from resolve_connect import get_context              # noqa: E402
from silence import detect_silences, ffmpeg_available  # noqa: E402
from transcribe import transcribe_media              # noqa: E402
from fillers import build_filler_set, detect_filler_intervals  # noqa: E402
from intervals import keep_intervals                 # noqa: E402
from clips import read_v1_clips, clip_source_range_s, rebuild_from_keeps  # noqa: E402

DEFAULTS = {
    "cut_silences": True,
    "noise_db": -30,
    "min_silence_dur": 0.5,
    "silence_pad": 0.10,

    "remove_fillers": False,
    "filler_groups": ["hesitation", "verbal"],
    "filler_words": [],          # extra custom words
    "filler_pad": 0.02,          # trim a touch inside each filler

    "make_captions": False,
    "caption_language": "cs",
    "caption_max_len": 42,

    "min_keep_dur": 0.15,
    "suffix": " - AutoCut",
}


def _adjust(intervals, shrink):
    """Shrink each (s, e) by `shrink` on both sides; drop ones that vanish."""
    out = []
    for s, e in intervals:
        s2, e2 = s + shrink, e - shrink
        if e2 > s2:
            out.append((s2, e2))
    return out


def run(settings=None, log=print, resolve_app=None):
    cfg = dict(DEFAULTS)
    if settings:
        cfg.update(settings)

    resolve, project, media_pool, timeline = get_context(resolve_app)
    fps = float(timeline.GetSetting("timelineFrameRate") or
                project.GetSetting("timelineFrameRate") or 25)
    log(f"Timeline: {timeline.GetName()} @ {fps} fps")

    do_cut = cfg["cut_silences"] or cfg["remove_fillers"]
    current = timeline

    if do_cut:
        if cfg["cut_silences"] and not ffmpeg_available():
            raise RuntimeError("ffmpeg not found (brew install ffmpeg).")

        clips = read_v1_clips(timeline, fps, log=log)
        if not clips:
            raise RuntimeError("No usable clips on video track 1.")
        log(f"Processing {len(clips)} clip(s)...")

        filler_set = (build_filler_set(cfg["filler_groups"], cfg["filler_words"])
                      if cfg["remove_fillers"] else set())
        words_cache = {}
        clip_keeps = []

        for clip in clips:
            cs, ce = clip_source_range_s(clip)
            cuts = []

            if cfg["cut_silences"]:
                sil = detect_silences(clip["path"], cfg["noise_db"], cfg["min_silence_dur"])
                cuts += _adjust(sil, cfg["silence_pad"])

            if cfg["remove_fillers"] and filler_set:
                if clip["path"] not in words_cache:
                    log(f"  Transcribing {os.path.basename(clip['path'])} for fillers...")
                    words_cache[clip["path"]] = transcribe_media(
                        clip["path"], language=cfg["caption_language"], max_len=1, log=log)
                fills = detect_filler_intervals(words_cache[clip["path"]], filler_set)
                cuts += _adjust(fills, cfg["filler_pad"])

            keeps = keep_intervals(cuts, cs, ce, pad=0.0, min_keep_dur=cfg["min_keep_dur"])
            removed = (ce - cs) - sum(b - a for a, b in keeps)
            log(f"  [{clip['index']}] {len(cuts)} cut(s), {len(keeps)} keep(s), "
                f"~{removed:.1f}s removed.")
            clip_keeps.append((clip, keeps))

        current = rebuild_from_keeps(media_pool, project, timeline, clip_keeps,
                                     cfg["suffix"], log)

    if cfg["make_captions"]:
        log("Generating captions on the current timeline...")
        captions_mod.run(
            settings={"language": cfg["caption_language"], "max_len": cfg["caption_max_len"]},
            log=log, resolve_app=resolve)

    log("AutoCut finished.")
    return current
