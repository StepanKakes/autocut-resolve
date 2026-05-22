"""Shared helpers for reading timeline clips and rebuilding a cut timeline."""

import os
import shutil
import subprocess

from silence import find_ffmpeg


def find_ffprobe():
    found = shutil.which("ffprobe")
    if found:
        return found
    ff = find_ffmpeg()
    if ff:
        cand = ff.replace("ffmpeg", "ffprobe")
        if os.path.exists(cand):
            return cand
    return None


def probe_fps(path):
    """True source frame rate from the media file itself (authoritative).

    Resolve's GetClipProperty('FPS') can disagree with the real rate when the
    clip is conformed to a different timeline rate, which throws word timing
    off; the source media's own rate is what AppendToTimeline frames use.
    """
    fp = find_ffprobe()
    if not fp:
        return None
    try:
        out = subprocess.run(
            [fp, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=r_frame_rate",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        ).stdout.strip()
        if "/" in out:
            num, den = out.split("/")
            return float(num) / float(den)
        return float(out)
    except Exception:
        return None


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
        prop_fps = source_fps(mpi, fps)
        true_fps = probe_fps(path) or prop_fps  # media's real rate is authoritative
        s_start = int(item.GetSourceStartFrame())
        s_end = int(item.GetSourceEndFrame())
        t_start = int(item.GetStart())
        t_end = int(item.GetEnd())
        try:
            t_dur = int(item.GetDuration())
        except Exception:
            t_dur = t_end - t_start
        src_span = s_end - s_start
        tl_span = t_end - t_start
        eff_fps = (src_span / (tl_span / fps)) if tl_span else 0
        log(f"  [{idx}] {os.path.basename(path)}: srcStart={s_start} srcEnd={s_end} "
            f"(span {src_span}) | tlStart={t_start} tlEnd={t_end} dur={t_dur} (span {tl_span}) "
            f"| propFPS={prop_fps} probeFPS={true_fps:.3f} timelineFPS={fps} "
            f"=> effFPS={eff_fps:.3f}")
        clips.append({
            "index": idx,
            "item": item,
            "mpi": mpi,
            "path": path,
            "src_fps": true_fps,
            "src_start_frame": s_start,
            "src_end_frame": s_end,  # inclusive
            "rec_start_frame": int(item.GetStart()),          # position on the timeline
        })
    return clips


def clip_source_range_s(clip):
    """The portion of the source used by this clip, in seconds."""
    fps = clip["src_fps"]
    return clip["src_start_frame"] / fps, (clip["src_end_frame"] + 1) / fps


def rebuild_from_keeps(media_pool, project, base_name, clip_keeps, suffix, log):
    """Create a new timeline containing only the kept segments.

    base_name: name of the original timeline (suffix is appended to it).
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

    target = base_name + suffix

    # Build under a temporary unique name first: a timeline named `target` may
    # already exist (previous run / live preview) and can't be deleted while it
    # is the current timeline, nor can we create a duplicate name.
    tmp = target + " ~build"
    new_timeline = media_pool.CreateEmptyTimeline(tmp)
    i = 2
    while new_timeline is None and i < 50:
        new_timeline = media_pool.CreateEmptyTimeline(f"{tmp} {i}")
        i += 1
    if new_timeline is None:
        raise RuntimeError(f"Could not create timeline for '{target}'.")

    project.SetCurrentTimeline(new_timeline)
    media_pool.AppendToTimeline(clip_infos)

    # Now that the new timeline is current, remove any old ones with the final
    # name, then rename ours to it.
    stale = _timelines_named(project, target)
    if stale:
        try:
            media_pool.DeleteTimelines(stale)
        except Exception as exc:
            log(f"  (could not delete old '{target}': {exc})")
    if not new_timeline.SetName(target):
        log(f"  (kept name '{new_timeline.GetName()}'; rename to '{target}' failed)")

    log(f"Created '{new_timeline.GetName()}' with {len(clip_infos)} segment(s).")
    return new_timeline


def _timelines_named(project, name):
    out = []
    for idx in range(1, (project.GetTimelineCount() or 0) + 1):
        tl = project.GetTimelineByIndex(idx)
        if tl and tl.GetName() == name:
            out.append(tl)
    return out
