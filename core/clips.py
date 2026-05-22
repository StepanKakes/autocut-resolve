"""Shared helpers for reading timeline clips and rebuilding a cut timeline."""

import os


def clip_property(obj, key, default=None):
    try:
        val = obj.GetClipProperty(key)
        return val if val not in (None, "") else default
    except Exception:
        return default


def source_fps(mpi, fallback):
    raw = clip_property(mpi, "FPS", fallback)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(fallback)


def read_v1_clips(timeline, fps, video_track=1, log=print):
    """Return clip dicts for each usable clip on the given video track."""
    items = timeline.GetItemListInTrack("video", video_track) or []
    clips = []
    for idx, item in enumerate(items, 1):
        mpi = item.GetMediaPoolItem()
        if mpi is None:
            log(f"  [{idx}] skipped (no media pool item).")
            continue
        path = clip_property(mpi, "File Path")
        if not path or not os.path.exists(path):
            log(f"  [{idx}] skipped (source not found: {path}).")
            continue
        sfps = source_fps(mpi, fps)
        clips.append({
            "index": idx,
            "item": item,
            "mpi": mpi,
            "path": path,
            "src_fps": sfps,
            "src_start_frame": int(item.GetSourceStartFrame()),
            "src_end_frame": int(item.GetSourceEndFrame()),  # inclusive
            "rec_start_frame": int(item.GetStart()),          # position on the timeline
        })
    return clips


def clip_source_range_s(clip):
    """The portion of the source used by this clip, in seconds."""
    fps = clip["src_fps"]
    return clip["src_start_frame"] / fps, (clip["src_end_frame"] + 1) / fps


def rebuild_from_keeps(media_pool, project, source_timeline, clip_keeps, suffix, log):
    """Create a new timeline containing only the kept segments.

    clip_keeps: list of (clip_dict, [(start_s, end_s), ...]) keep intervals in
    source seconds.
    """
    clip_infos = []
    for clip, keeps in clip_keeps:
        fps = clip["src_fps"]
        for a, b in keeps:
            sf = int(round(a * fps))
            ef = int(round(b * fps)) - 1  # AppendToTimeline endFrame is inclusive
            if ef <= sf:
                continue
            clip_infos.append({
                "mediaPoolItem": clip["mpi"],
                "startFrame": sf,
                "endFrame": ef,
            })

    if not clip_infos:
        raise RuntimeError("Nothing left to keep -- check thresholds.")

    new_name = source_timeline.GetName() + suffix
    new_timeline = media_pool.CreateEmptyTimeline(new_name)
    if new_timeline is None:
        raise RuntimeError(f"Could not create timeline '{new_name}'.")
    project.SetCurrentTimeline(new_timeline)
    media_pool.AppendToTimeline(clip_infos)
    log(f"Created '{new_name}' with {len(clip_infos)} segment(s).")
    return new_timeline
