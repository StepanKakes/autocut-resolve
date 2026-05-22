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


def _set_audio_render_format(resolve, project, log):
    """Configure a WAV (or any audio) render. Returns True on success.

    SetCurrentRenderFormatAndCodec only works reliably with the Deliver page
    open, and the accepted codec token varies by build -- so we try several
    combinations and fall back to any built-in audio render preset.
    """
    resolve.OpenPage("deliver")

    formats = project.GetRenderFormats() or {}
    log(f"Render formats: {formats}")
    wav_token = next((tok for _, tok in formats.items() if str(tok).lower() == "wav"), "wav")
    codecs = project.GetRenderCodecs(wav_token) or {}
    log(f"WAV codecs: {codecs}")

    combos = []
    combos += [(wav_token, c) for c in codecs.values()]
    combos += [(wav_token, "lpcm"), (wav_token, "LinearPCM"), (wav_token, "")]
    for fmt, codec in combos:
        if project.SetCurrentRenderFormatAndCodec(fmt, codec):
            log(f"Using render format '{fmt}', codec '{codec}'.")
            return True

    presets = project.GetRenderPresetList() or []
    log(f"Format/codec failed; trying audio presets from: {presets}")
    for preset in presets:
        if "audio" in str(preset).lower():
            if project.LoadRenderPreset(preset):
                log(f"Loaded render preset '{preset}'.")
                return True
    return False


def _render_timeline_audio(resolve, project, out_dir, log):
    if not _set_audio_render_format(resolve, project, log):
        raise RuntimeError("Could not set an audio render format (see formats above).")
    project.SetCurrentRenderMode(1)  # single clip
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

    # SRT cue times are relative to 0, but AppendToTimeline drops clips at the
    # END of the timeline by default. Pin the subtitle clip to the timeline's
    # start frame so the cues line up with the footage.
    start_frame = int(timeline.GetStartFrame())
    clip_info = [{"mediaPoolItem": item, "recordFrame": start_frame} for item in imported]
    appended = media_pool.AppendToTimeline(clip_info)
    n = len(appended) if isinstance(appended, list) else len(segments)
    log(f"Done. Added {n} subtitle(s) starting at frame {start_frame}. SRT: {srt_path}")
    return srt_path


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        print(f"AutoCut captions error: {exc}")
        raise
