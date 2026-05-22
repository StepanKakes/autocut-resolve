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
from repeats import detect_repeat_intervals           # noqa: E402
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

    "remove_repeats": False,
    "repeat_threshold": 0.8,     # text-similarity to treat segments as re-takes
    "repeat_pad": 0.05,

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


def _in_any(t, intervals):
    return any(a <= t <= b for a, b in intervals)


def analyze(settings=None, log=print, resolve_app=None):
    """Transcribe the timeline and tag each word as keep/cut with a reason.

    Returns a dict the UI can render as an editable transcript:
        {
          "fps": float,
          "clips": [ {"clip": clip_dict, "src_range": (cs, ce),
                      "silences": [(s,e)...],   # source seconds
                      "words": [ {text,start,end,cut,reason}, ... ]} ],
        }
    Detection is non-destructive -- nothing changes in Resolve until apply().
    """
    cfg = dict(DEFAULTS)
    if settings:
        cfg.update(settings)

    resolve, project, media_pool, timeline = get_context(resolve_app)
    fps = float(timeline.GetSetting("timelineFrameRate") or
                project.GetSetting("timelineFrameRate") or 25)
    log(f"Timeline: {timeline.GetName()} @ {fps} fps")

    clips = read_v1_clips(timeline, fps, log=log)
    if not clips:
        raise RuntimeError("No usable clips on video track 1.")

    filler_set = (build_filler_set(cfg["filler_groups"], cfg["filler_words"])
                  if cfg["remove_fillers"] else set())
    word_cache, phrase_cache = {}, {}
    out_clips = []

    for clip in clips:
        cs, ce = clip_source_range_s(clip)
        path = clip["path"]

        if path not in word_cache:
            log(f"  Transcribing {os.path.basename(path)}...")
            word_cache[path] = transcribe_media(path, language=cfg["caption_language"],
                                                max_len=1, log=log)
        words = [dict(w, cut=False, reason="") for w in word_cache[path]
                 if w["end"] > cs and w["start"] < ce]

        fil_ints = detect_filler_intervals(words, filler_set) if filler_set else []

        rep_ints = []
        if cfg["remove_repeats"]:
            if path not in phrase_cache:
                phrase_cache[path] = transcribe_media(path, language=cfg["caption_language"],
                                                      max_len=0, log=log)
            segs = [s for s in phrase_cache[path] if s["end"] > cs and s["start"] < ce]
            rep_ints = detect_repeat_intervals(segs, threshold=cfg["repeat_threshold"])

        for w in words:
            mid = (w["start"] + w["end"]) / 2
            if _in_any(mid, fil_ints):
                w["cut"], w["reason"] = True, "filler"
            elif _in_any(mid, rep_ints):
                w["cut"], w["reason"] = True, "repeat"

        silences = detect_silences(path, cfg["noise_db"], cfg["min_silence_dur"]) \
            if cfg["cut_silences"] else []

        out_clips.append({"clip": clip, "src_range": (cs, ce),
                          "silences": silences, "words": words})

    n_cut = sum(1 for c in out_clips for w in c["words"] if w["cut"])
    log(f"Analysis done: {sum(len(c['words']) for c in out_clips)} words, "
        f"{n_cut} marked for removal.")
    return {"fps": fps, "clips": out_clips, "timeline_name": timeline.GetName()}


def apply(analysis, settings=None, log=print, resolve_app=None, replace_timeline=None):
    """Rebuild the timeline from an analysis whose word `cut` flags may have been
    edited by the user. Cuts = flagged words (+ silences if enabled).

    If `replace_timeline` is given, it is deleted after the new one is built --
    used by the live/auto-apply mode so previews don't pile up.
    """
    cfg = dict(DEFAULTS)
    if settings:
        cfg.update(settings)

    resolve, project, media_pool, timeline = get_context(resolve_app)
    clip_keeps = []
    for entry in analysis["clips"]:
        clip = entry["clip"]
        cs, ce = entry["src_range"]
        cuts = []
        word_cuts = [(w["start"], w["end"]) for w in entry["words"] if w["cut"]]
        cuts += _adjust(word_cuts, -cfg["filler_pad"])  # widen a touch to remove cleanly
        if cfg["cut_silences"]:
            cuts += _adjust(entry["silences"], cfg["silence_pad"])
        keeps = keep_intervals(cuts, cs, ce, pad=0.0, min_keep_dur=cfg["min_keep_dur"])
        removed = (ce - cs) - sum(b - a for a, b in keeps)
        log(f"  [{clip['index']}] {len(cuts)} cut(s), {len(keeps)} keep(s), ~{removed:.1f}s removed.")
        clip_keeps.append((clip, keeps))

    base_name = analysis.get("timeline_name") or timeline.GetName()
    current = rebuild_from_keeps(media_pool, project, base_name, clip_keeps, cfg["suffix"], log)

    if replace_timeline is not None and replace_timeline != current:
        try:
            media_pool.DeleteTimelines([replace_timeline])
        except Exception as exc:
            log(f"  (could not delete previous preview: {exc})")

    if cfg["make_captions"]:
        log("Generating captions on the new timeline...")
        captions_mod.run(settings={"language": cfg["caption_language"],
                                   "max_len": cfg["caption_max_len"]},
                         log=log, resolve_app=resolve)
    log("AutoCut finished.")
    return current


def run(settings=None, log=print, resolve_app=None):
    cfg = dict(DEFAULTS)
    if settings:
        cfg.update(settings)

    resolve, project, media_pool, timeline = get_context(resolve_app)
    fps = float(timeline.GetSetting("timelineFrameRate") or
                project.GetSetting("timelineFrameRate") or 25)
    log(f"Timeline: {timeline.GetName()} @ {fps} fps")

    do_cut = cfg["cut_silences"] or cfg["remove_fillers"] or cfg["remove_repeats"]
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
        words_cache = {}      # word-level transcription (fillers)
        phrase_cache = {}     # phrase-level transcription (repeats)
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

            if cfg["remove_repeats"]:
                if clip["path"] not in phrase_cache:
                    log(f"  Transcribing {os.path.basename(clip['path'])} for repeats...")
                    phrase_cache[clip["path"]] = transcribe_media(
                        clip["path"], language=cfg["caption_language"], max_len=0, log=log)
                segs = [s for s in phrase_cache[clip["path"]]
                        if s["end"] > cs and s["start"] < ce]
                reps = detect_repeat_intervals(segs, threshold=cfg["repeat_threshold"])
                cuts += _adjust(reps, -cfg["repeat_pad"])  # widen slightly to fully remove

            keeps = keep_intervals(cuts, cs, ce, pad=0.0, min_keep_dur=cfg["min_keep_dur"])
            removed = (ce - cs) - sum(b - a for a, b in keeps)
            log(f"  [{clip['index']}] {len(cuts)} cut(s), {len(keeps)} keep(s), "
                f"~{removed:.1f}s removed.")
            clip_keeps.append((clip, keeps))

        current = rebuild_from_keeps(media_pool, project, timeline.GetName(), clip_keeps,
                                     cfg["suffix"], log)

    if cfg["make_captions"]:
        log("Generating captions on the current timeline...")
        captions_mod.run(
            settings={"language": cfg["caption_language"], "max_len": cfg["caption_max_len"]},
            log=log, resolve_app=resolve)

    log("AutoCut finished.")
    return current
