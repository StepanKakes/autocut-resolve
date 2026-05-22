"""Auto cut silences: build a new timeline from the current one with silent
gaps removed.

Strategy (robust across Resolve versions): instead of split + ripple-delete on
the existing timeline, we read every clip on video track 1, detect silence in
each clip's source media, compute the segments to keep, and APPEND those
segments to a brand-new timeline via MediaPool.AppendToTimeline. The original
timeline is never touched.

Run from inside Resolve (Workspace > Scripts) or from a terminal with Resolve
running.
"""

import os
import sys

# Make sibling modules importable no matter how we're launched.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from resolve_connect import get_context           # noqa: E402
from silence import detect_silences, ffmpeg_available  # noqa: E402
from intervals import keep_intervals               # noqa: E402

# ---- Settings (will be driven by the panel UI later) -----------------------
SETTINGS = {
    "noise_db": -30,          # quieter than this = silence
    "min_silence_dur": 0.5,   # ignore gaps shorter than this (seconds)
    "pad": 0.10,              # keep this much audio around speech (seconds)
    "min_keep_dur": 0.15,     # drop kept slivers shorter than this (seconds)
    "video_track": 1,         # which video track to process
    "timeline_suffix": " - AutoCut",
}


def _clip_property(item_or_mpi, key, default=None):
    try:
        val = item_or_mpi.GetClipProperty(key)
        return val if val not in (None, "") else default
    except Exception:
        return default


def _source_fps(mpi, fallback):
    raw = _clip_property(mpi, "FPS", fallback)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(fallback)


def run(settings=None, log=print):
    cfg = dict(SETTINGS)
    if settings:
        cfg.update(settings)

    if not ffmpeg_available():
        raise RuntimeError("ffmpeg not found on PATH. Install it (brew install ffmpeg).")

    resolve, project, media_pool, timeline = get_context()

    timeline_fps = float(timeline.GetSetting("timelineFrameRate") or
                         project.GetSetting("timelineFrameRate") or 25)
    log(f"Timeline: {timeline.GetName()}  @ {timeline_fps} fps")

    items = timeline.GetItemListInTrack("video", cfg["video_track"]) or []
    if not items:
        raise RuntimeError(f"No clips on video track {cfg['video_track']}.")
    log(f"Found {len(items)} clip(s) on V{cfg['video_track']}.")

    clip_infos = []
    for idx, item in enumerate(items, 1):
        mpi = item.GetMediaPoolItem()
        if mpi is None:
            log(f"  [{idx}] skipped (no media pool item, e.g. compound/title).")
            continue
        path = _clip_property(mpi, "File Path")
        if not path or not os.path.exists(path):
            log(f"  [{idx}] skipped (source file not found: {path}).")
            continue

        src_fps = _source_fps(mpi, timeline_fps)
        src_start = int(item.GetSourceStartFrame())
        src_end = int(item.GetSourceEndFrame())  # inclusive last source frame
        clip_start_s = src_start / src_fps
        clip_end_s = (src_end + 1) / src_fps

        silences = detect_silences(path, cfg["noise_db"], cfg["min_silence_dur"])
        keeps = keep_intervals(
            silences, clip_start_s, clip_end_s,
            pad=cfg["pad"], min_keep_dur=cfg["min_keep_dur"],
        )
        removed = (clip_end_s - clip_start_s) - sum(b - a for a, b in keeps)
        log(f"  [{idx}] {os.path.basename(path)}: {len(silences)} silence(s), "
            f"{len(keeps)} keep segment(s), ~{removed:.1f}s removed.")

        for a, b in keeps:
            sf = int(round(a * src_fps))
            ef = int(round(b * src_fps)) - 1  # AppendToTimeline endFrame is inclusive
            if ef <= sf:
                continue
            clip_infos.append({
                "mediaPoolItem": mpi,
                "startFrame": sf,
                "endFrame": ef,
            })

    if not clip_infos:
        raise RuntimeError("Nothing to keep -- check noise threshold / min silence.")

    new_name = timeline.GetName() + cfg["timeline_suffix"]
    new_timeline = media_pool.CreateEmptyTimeline(new_name)
    if new_timeline is None:
        raise RuntimeError(f"Could not create timeline '{new_name}'.")
    project.SetCurrentTimeline(new_timeline)

    appended = media_pool.AppendToTimeline(clip_infos)
    n = len(appended) if isinstance(appended, list) else (len(clip_infos) if appended else 0)
    log(f"Done. Created '{new_name}' with {n} segment(s).")
    return new_timeline


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # surface a readable message in the Resolve console
        print(f"AutoCut error: {exc}")
        raise
