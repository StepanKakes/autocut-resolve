"""Czech filler-word detection from word-level transcription.

Returns the time intervals (seconds) occupied by filler words/phrases so the
caller can cut them out. Supports multi-word fillers ("víš co", "že jo").
"""

# Default filler vocabulary, grouped. The UI lets the user toggle groups and
# add their own words.
FILLER_GROUPS = {
    "hesitation": ["ehm", "ehmm", "hmm", "hm", "em", "eh", "mmm", "éé", "ee"],
    "verbal": ["jako", "jakože", "jakoby", "prostě", "vlastně"],
    "phrases": ["víš co", "víš", "že jo", "no jako"],
    "connectors": ["no", "tak", "takže", "teda", "tedy"],
}

DEFAULT_ACTIVE_GROUPS = ["hesitation", "verbal"]

_STRIP = " \t\n.,!?…\"'»«:;-–—()"


def normalize(word):
    """Lowercase and strip surrounding punctuation; keep Czech diacritics."""
    return word.strip(_STRIP).lower()


def build_filler_set(active_groups=None, extra_words=None):
    """Return a set of normalized filler phrases from groups + custom words."""
    groups = active_groups if active_groups is not None else DEFAULT_ACTIVE_GROUPS
    fillers = set()
    for g in groups:
        for w in FILLER_GROUPS.get(g, []):
            fillers.add(normalize(w))
    for w in (extra_words or []):
        n = normalize(w)
        if n:
            fillers.add(n)
    fillers.discard("")
    return fillers


def detect_filler_intervals(words, fillers):
    """Find filler intervals in a list of word dicts.

    Args:
        words: list of {"start": s, "end": s, "text": str} (one entry per word).
        fillers: set of normalized phrases (1 or 2 words) from build_filler_set.

    Returns:
        List of (start_s, end_s) intervals covering matched fillers.
    """
    if not fillers:
        return []
    max_n = max((len(f.split()) for f in fillers), default=1)
    norms = [normalize(w["text"]) for w in words]

    intervals = []
    i = 0
    n = len(words)
    while i < n:
        matched = False
        for length in range(min(max_n, n - i), 0, -1):
            phrase = " ".join(norms[i:i + length]).strip()
            if phrase and phrase in fillers:
                intervals.append((words[i]["start"], words[i + length - 1]["end"]))
                i += length
                matched = True
                break
        if not matched:
            i += 1
    return intervals
