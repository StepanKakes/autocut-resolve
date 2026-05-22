"""Auto captions: transcribe the current timeline and add subtitles.

Flow: render the timeline's audio to a WAV (so caption times line up with the
timeline, not the raw source) -> whisper.cpp transcription -> SRT -> import the
SRT and drop it on a subtitle track.
"""

import glob
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from resolve_connect import get_context          # noqa: E402
from transcribe import transcribe_wav             # noqa: E402
from srt import write_srt                          # noqa: E402

SETTINGS = {
    "language": "cs",
    "max_len": 42,        # max characters per subtitle (0 = whisper default)
    "add_subtitle_track": True,
}


def _pick_audio_format(project, log):
    """Find a WAV render format/codec the project actually supports."""
    formats = project.GetRenderFormats() or {}
    # formats maps {displayName: extension}
    for name, ext in formats.items():
        if str(ext).lower() == "wav":
            codecs = project.GetRenderCodecs(name) or {}
            codec = next(iter(codecs.values()), "")
            log(f"Audio render format: '{name}' (.{ext}), codec '{codec}'.")
            return name, codec
    raise RuntimeError(f"No WAV render format available. Formats: {list(formats)}")


def _render_timeline_audio(resolve, project, out_dir, log):
    fmt, codec = _pick_audio_format(project, log)
    project.SetCurrentRenderMode(1)  # single clip
    if not project.SetCurrentRenderFormatAndCodec(fmt, codec):
        raise RuntimeError(f"SetCurrentRenderFormatAndCodec({fmt}, {codec}) failed.")
    project.SetRenderSettings({
        "SelectAllFrames": True,
        "TargetDir": out_dir,
        "CustomName": "autocut_audio",
        "ExportVideo": False,
        "ExportAudio": True,
    })
    project.DeleteAllRenderJobs()
    job_id = project.AddRenderJob()
    if not job_id:
        raise RuntimeError("AddRenderJob failed (check render settings).")

    log("Rendering timeline audio...")
    project.StartRendering(job_id)
    while project.IsRenderingInProgress():
        time.sleep(0.4)

    status = project.GetRenderJobStatus(job_id) or {}
    if status.get("JobStatus") not in (None, "Complete"):
        log(f"Render status: {status}")

    matches = glob.glob(os.path.join(out_dir, "autocut_audio*.wav"))
    if not matches:
        raise RuntimeError(f"Rendered WAV not found in {out_dir}.")
    return matches[0]


def run(settings=None, log=print, resolve_app=None):
    cfg = dict(SETTINGS)
    if settings:
        cfg.update(settings)

    resolve, project, media_pool, timeline = get_context(resolve_app)
    log(f"Timeline: {timeline.GetName()}")

    tmp = tempfile.mkdtemp(prefix="autocut_")
    wav = _render_timeline_audio(resolve, project, tmp, log)

    segments = transcribe_wav(wav, language=cfg["language"], max_len=cfg["max_len"], log=log)
    if not segments:
        raise RuntimeError("Transcription returned no segments.")
    log(f"Transcribed {len(segments)} caption segment(s).")

    srt_path = os.path.join(tmp, "captions.srt")
    write_srt(segments, srt_path)

    if cfg["add_subtitle_track"]:
        timeline.AddTrack("subtitle")

    imported = media_pool.ImportMedia([srt_path]) or []
    if not imported:
        raise RuntimeError(f"ImportMedia failed for {srt_path}. Import it manually.")
    appended = media_pool.AppendToTimeline(imported)
    n = len(appended) if isinstance(appended, list) else len(segments)
    log(f"Done. Added {n} subtitle(s). SRT saved at: {srt_path}")
    return srt_path


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        print(f"AutoCut captions error: {exc}")
        raise
