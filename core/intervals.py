"""Pure interval math: turn detected silences into 'keep' segments.

Kept deliberately free of any Resolve/ffmpeg dependency so it can be unit-tested
in isolation.
"""


def keep_intervals(silences, clip_start_s, clip_end_s, pad=0.10, min_keep_dur=0.15):
    """Compute the segments to KEEP (the complement of padded silences).

    Args:
        silences: list of (start_s, end_s) silent intervals, absolute seconds.
        clip_start_s, clip_end_s: the portion of the source actually used by the
            timeline clip, in absolute seconds.
        pad: keep this many seconds of audio on each side of speech, so cuts
            don't clip the start/end of words (shrinks each silence by `pad`).
        min_keep_dur: drop kept segments shorter than this (avoids 1-frame slivers).

    Returns:
        List of (start_s, end_s) keep intervals, clamped to the clip range.
    """
    # Shrink silences by pad on both ends; drop ones that vanish.
    cuts = []
    for s, e in silences:
        s2, e2 = s + pad, e - pad
        if e2 > s2:
            cuts.append((s2, e2))
    cuts.sort()

    keeps = []
    pos = clip_start_s
    for s, e in cuts:
        s = max(s, clip_start_s)
        e = min(e, clip_end_s)
        if e <= clip_start_s or s >= clip_end_s or e <= s:
            continue
        if s > pos:
            keeps.append((pos, s))
        pos = max(pos, e)
    if pos < clip_end_s:
        keeps.append((pos, clip_end_s))

    return [(a, b) for (a, b) in keeps if (b - a) >= min_keep_dur]
