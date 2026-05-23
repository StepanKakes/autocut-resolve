"""Detect repeated takes (the speaker restarts a sentence) and mark the earlier
attempts for removal, keeping the last one.

Works on phrase-level transcription segments. Consecutive segments whose text is
similar above a threshold are treated as re-takes of the same line.
"""

import difflib

from fillers import normalize


def _norm_text(text):
    return " ".join(normalize(w) for w in text.split()).strip()


def _similarity(a, b):
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def detect_take_groups(segments, threshold=0.8, min_words=3):
    """Find groups of consecutive similar takes (multiple attempts at the same
    sentence). Each group is a list of takes; the last one is selected by
    default. Used so the user can pick the best attempt per group instead of
    auto-cutting all-but-last.
    """
    norms = [_norm_text(s["text"]) for s in segments]
    groups = []
    n = len(segments)
    i = 0
    while i < n:
        j = i
        while (j + 1 < n
               and len(norms[j].split()) >= min_words
               and _similarity(norms[j], norms[j + 1]) >= threshold):
            j += 1
        if j > i:
            takes = [{
                "start": segments[k]["start"],
                "end": segments[k]["end"],
                "text": segments[k]["text"],
                "selected": (k == j),  # default: keep the last attempt
            } for k in range(i, j + 1)]
            groups.append(takes)
            i = j + 1
        else:
            i += 1
    return groups


def detect_repeat_intervals(segments, threshold=0.8, min_words=3):
    """Find intervals of failed/earlier takes to cut.

    Args:
        segments: list of {"start": s, "end": s, "text": str} (phrase-level).
        threshold: 0..1 text-similarity above which two segments are "the same".
        min_words: ignore very short segments (avoids matching filler phrases).

    Returns:
        List of (start_s, end_s) intervals for the earlier takes (keep the last).
    """
    norms = [_norm_text(s["text"]) for s in segments]
    cuts = []
    n = len(segments)
    i = 0
    while i < n:
        # extend a run of consecutive similar segments starting at i
        j = i
        while (j + 1 < n
               and len(norms[j].split()) >= min_words
               and _similarity(norms[j], norms[j + 1]) >= threshold):
            j += 1
        if j > i:  # segments i..j are re-takes; keep the last (j), cut the rest
            for k in range(i, j):
                cuts.append((segments[k]["start"], segments[k]["end"]))
            i = j + 1
        else:
            i += 1
    return cuts
