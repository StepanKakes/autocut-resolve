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
from transcribe import transcribe_media, Cancelled  # noqa: E402
from fillers import build_filler_set, detect_filler_intervals  # noqa: E402
from repeats import detect_repeat_intervals, detect_take_groups  # noqa: E402
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
    "caption_max_words": 0,
    "caption_keep_punct": True,
    "caption_case": "asis",

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


def _phrases_from_words(words):
    """Group word-level entries into sentence-ish phrases for repeat detection
    (avoids a second transcription pass)."""
    phrases, cur = [], []
    for w in words:
        cur.append(w)
        if w["text"].strip().endswith((".", "?", "!", "…")):
            phrases.append({"start": cur[0]["start"], "end": cur[-1]["end"],
                            "text": " ".join(x["text"] for x in cur)})
            cur = []
    if cur:
        phrases.append({"start": cur[0]["start"], "end": cur[-1]["end"],
                        "text": " ".join(x["text"] for x in cur)})
    return phrases


def apply_detection(words, phrases, take_groups, cfg):
    """(Re)compute each word's auto cut flag from filler + take-selection settings,
    keeping any manual override. Pure text/time ops -- instant; runs every time
    a filter option changes or the user picks a different take.
    """
    filler_set = build_filler_set(cfg.get("filler_groups", []), cfg.get("filler_words", []))
    fil_ints = detect_filler_intervals(words, filler_set) if filler_set else []

    # Cut intervals from take selection: every UN-selected take in every group.
    take_cuts = []
    if cfg.get("remove_repeats") and take_groups:
        for group in take_groups:
            for take in group:
                if not take["selected"]:
                    take_cuts.append((take["start"], take["end"]))

    for w in words:
        mid = (w["start"] + w["end"]) / 2
        if _in_any(mid, fil_ints):
            w["auto_cut"], w["auto_reason"] = True, "filler"
        elif _in_any(mid, take_cuts):
            w["auto_cut"], w["auto_reason"] = True, "take"
        else:
            w["auto_cut"], w["auto_reason"] = False, ""
        manual = w.get("manual")
        if manual is not None:                 # user clicked this word: respect it
            w["cut"], w["reason"] = manual, ("manual" if manual else "")
        else:
            w["cut"], w["reason"] = w["auto_cut"], w["auto_reason"]


def redetect(analysis, settings=None):
    """Recompute cut flags across an existing analysis for new filter settings,
    without re-transcribing. Returns the number of words now marked for removal."""
    cfg = dict(DEFAULTS)
    if settings:
        cfg.update(settings)
    n = 0
    for entry in analysis["clips"]:
        apply_detection(entry["words"], entry["phrases"],
                        entry.get("take_groups", []), cfg)
        n += sum(1 for w in entry["words"] if w["cut"])
    return n


def analyze(settings=None, log=print, resolve_app=None, cancel=None):
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
    tl_start_frame = int(timeline.GetStartFrame())
    log(f"Timeline: {timeline.GetName()} @ {fps} fps")

    if timeline.GetName().endswith(cfg["suffix"]):
        log("⚠️  POZOR: analyzuješ už ořezanou timeline (" + timeline.GetName() +
            "). Přepni na PŮVODNÍ timeline a spusť Analyzovat znovu!")

    clips = read_v1_clips(timeline, fps, log=log)
    if not clips:
        raise RuntimeError("No usable clips on video track 1.")
    if len(clips) > 5:
        log(f"⚠️  POZOR: timeline má {len(clips)} klipů — vypadá jako už nastříhaná. "
            f"Editor patří na PŮVODNÍ (jeden souvislý klip).")

    word_cache = {}
    out_clips = []

    for clip in clips:
        if cancel is not None and cancel.is_set():
            raise Cancelled("zrušeno uživatelem")
        cs, ce = clip_source_range_s(clip)
        path = clip["path"]

        # Transcribe ONLY the source range this clip uses. Transcribing the whole
        # (possibly very long) file lets whisper timestamps drift, which shifts
        # cuts; a short local pass stays accurate. Times come back 0-based for the
        # extract, so we add `cs` to get absolute source seconds.
        key = (path, round(cs, 2), round(ce, 2))
        if key not in word_cache:
            log(f"  Transcribing {os.path.basename(path)} [{cs:.1f}-{ce:.1f}s]...")
            wl = transcribe_media(path, language=cfg["caption_language"],
                                  start_s=cs, dur_s=ce - cs, max_len=1, log=log, cancel=cancel)
            word_cache[key] = [dict(w, start=w["start"] + cs, end=w["end"] + cs) for w in wl]
        words = [dict(w, auto_cut=False, auto_reason="", manual=None, cut=False, reason="")
                 for w in word_cache[key]]
        # Stamp each word with its position on the *timeline*, so the panel can
        # karaoke-highlight the current word during playback.
        rec_offset_s = (clip["rec_start_frame"] - tl_start_frame) / fps
        for w in words:
            w["tl_start"] = rec_offset_s + (w["start"] - cs)
            w["tl_end"] = rec_offset_s + (w["end"] - cs)
        phrases = _phrases_from_words(words)
        take_groups = detect_take_groups(phrases, threshold=cfg["repeat_threshold"])

        # Always transcribe silence info so the silence checkbox works without
        # re-analyzing (it's cheap energy analysis, not whisper).
        silences = detect_silences(path, cfg["noise_db"], cfg["min_silence_dur"])

        entry = {"clip": clip, "src_range": (cs, ce), "silences": silences,
                 "words": words, "phrases": phrases, "take_groups": take_groups}
        apply_detection(words, phrases, take_groups, cfg)
        out_clips.append(entry)

    n_cut = sum(1 for c in out_clips for w in c["words"] if w["cut"])
    log(f"Analysis done: {sum(len(c['words']) for c in out_clips)} words, "
        f"{n_cut} marked for removal.")
    return {"fps": fps, "clips": out_clips,
            "timeline_name": timeline.GetName(),
            "timeline_start_frame": tl_start_frame}


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
    # rebuild_from_keeps already deletes any existing timeline with the target
    # name, so the previous preview is replaced cleanly.
    current = rebuild_from_keeps(media_pool, project, base_name, clip_keeps, cfg["suffix"], log)

    # Recompute timeline positions for KEPT words on the freshly built cut
    # timeline, so the karaoke playhead keeps tracking after Resolve switches
    # to it. Cut words get None (they're not on the new timeline).
    t0 = 0.0
    for (_clip, keeps), entry in zip(clip_keeps, analysis["clips"]):
        for w in entry["words"]:
            if w["cut"]:
                w["cut_tl_start"] = w["cut_tl_end"] = None
                continue
            mid = (w["start"] + w["end"]) / 2
            t_acc = t0
            placed = False
            for a, b in keeps:
                if a <= mid <= b:
                    ws = max(w["start"], a)
                    we = min(w["end"], b)
                    w["cut_tl_start"] = t_acc + (ws - a)
                    w["cut_tl_end"] = t_acc + (we - a)
                    placed = True
                    break
                t_acc += (b - a)
            if not placed:
                w["cut_tl_start"] = w["cut_tl_end"] = None
        for a, b in keeps:
            t0 += (b - a)
    try:
        analysis["cut_timeline_name"] = current.GetName()
        analysis["cut_timeline_start_frame"] = int(current.GetStartFrame())
        analysis["cut_fps"] = float(current.GetSetting("timelineFrameRate")
                                    or analysis.get("fps", 25))
    except Exception as exc:
        log(f"  (could not capture cut timeline meta: {exc})")

    if cfg["make_captions"]:
        log("Adding captions from the existing transcript (no re-transcription)...")
        segs = build_caption_segments(analysis, cfg)
        captions_mod.place_segments(media_pool, current, segs,
                                    int(current.GetStartFrame()), log=log)
    log("AutoCut finished.")
    return current


def build_caption_segments(analysis, cfg):
    """Caption segments (timeline-relative seconds) built from the analysis
    transcript -- KEPT words positioned to match the cut timeline. Reuses the
    transcription instead of running whisper again."""
    words_tl = []
    t0 = 0.0  # running position on the rebuilt timeline
    for entry in analysis["clips"]:
        cs, ce = entry["src_range"]
        cuts = _adjust([(w["start"], w["end"]) for w in entry["words"] if w["cut"]],
                       -cfg["filler_pad"])
        if cfg["cut_silences"]:
            cuts += _adjust(entry["silences"], cfg["silence_pad"])
        keeps = keep_intervals(cuts, cs, ce, pad=0.0, min_keep_dur=cfg["min_keep_dur"])
        kept = [w for w in entry["words"] if not w["cut"]]
        for a, b in keeps:
            for w in kept:
                mid = (w["start"] + w["end"]) / 2
                if a <= mid <= b:
                    words_tl.append({"start": t0 + (max(w["start"], a) - a),
                                     "end": t0 + (min(w["end"], b) - a),
                                     "text": w["text"]})
            t0 += (b - a)
    words_tl.sort(key=lambda x: x["start"])
    return captions_mod.group_and_format(words_tl, caption_settings(cfg))


def captions_from_analysis(analysis, settings=None, log=print, resolve_app=None):
    """Generate captions on the current timeline straight from the analysis
    transcript (used by the 'Vygenerovat titulky' button after an analyze)."""
    cfg = dict(DEFAULTS)
    if settings:
        cfg.update(settings)
    resolve, project, media_pool, timeline = get_context(resolve_app)
    total = sum(len(e["words"]) for e in analysis["clips"])
    cut = sum(1 for e in analysis["clips"] for w in e["words"] if w["cut"])
    log(f"Captions FROM TRANSCRIPT on '{timeline.GetName()}': "
        f"{total} words, {cut} cut, {total - cut} kept.")
    segs = build_caption_segments(analysis, cfg)
    log(f"-> {len(segs)} caption segment(s).")
    return captions_mod.place_segments(media_pool, timeline, segs,
                                       int(timeline.GetStartFrame()), log=log)


def caption_settings(cfg):
    """Extract the caption-related keys for captions.run()."""
    return {
        "language": cfg["caption_language"],
        "max_len": cfg["caption_max_len"],
        "max_words": cfg["caption_max_words"],
        "keep_punctuation": cfg["caption_keep_punct"],
        "case": cfg["caption_case"],
    }


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
