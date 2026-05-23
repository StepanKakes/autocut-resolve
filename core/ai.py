"""Ask the `claude` CLI (Claude Code in headless mode) to pick which words to
cut from the transcript -- failed takes, false starts, redundant fillers.

The function mutates the analysis in place by setting `manual=True` on every
word Claude proposes cutting; the existing live-redetect path then renders the
strikethrough and (if Živě is on) rebuilds the timeline.
"""

import json
import re
import shutil
import subprocess


_PROMPT = """Jsi profesionální video editor. Dole je přepis talking-head videa, kde mluvčí natáčel ve více pokusech. Tvůj úkol: označit slova, která se mají VYŘÍZNOUT, aby výsledné video bylo souvislé, plynulé a dávalo smysl.

Pravidla (přesně v tomto pořadí důležitosti):
1. Když je v sekci „Skupiny opakovaných pokusů" víc pokusů o stejnou větu, vyber JEDEN nejlepší (kompletní, plynulý, bez přeřeknutí) a všechna slova v OSTATNÍCH pokusech té skupiny označ k vyříznutí. Pozor: ostatní text mimo skupiny do toho NEZAhrnuj.
2. Vystřihni nedokončené začátky vět, přeřeknutí, falešné starty („Dneska vám ukáž — Dneska vám ukážu…", „já si myslím, že — že tohle…").
3. Vystřihni očividné zvukové vycpávky (ehm, hmm, em, eh).
4. Slovní vatu („prostě", „vlastně", „jako" jako berličku, „takže") vystřihni jen tam, kde reálně překáží — když dává smysl ve větě, NECH ji.
5. Konzervativně: když si nejsi jistý, slovo NECH. Lepší trochu vaty než rozsekaná věta.
6. Výsledek po vyříznutí musí být souvislý a gramaticky držet pohromadě.

Slova v přepisu (jedno na řádek, [index] text):
{words}

Skupiny opakovaných pokusů (algoritmicky detekované):
{groups}

VRAŤ POUZE JEDEN JSON objekt, BEZ jakéhokoliv vysvětlení nebo textu kolem. Formát přesně takto:
{{"cut_indices": [12, 13, 14, ...]}}
"""


def find_claude():
    return shutil.which("claude")


def claude_available():
    return find_claude() is not None


def _flat_words(analysis):
    return [w for entry in analysis["clips"] for w in entry["words"]]


def _build_groups_section(words, analysis):
    """Format the take-group section with flat-word index ranges."""
    lines = []
    counter = 0
    for entry in analysis["clips"]:
        for g in entry.get("take_groups", []) or []:
            if len(g) < 2:
                continue
            counter += 1
            lines.append(f"Skupina {counter}:")
            for ti, take in enumerate(g, 1):
                t0, t1 = take["start"], take["end"]
                idxs = [i for i, w in enumerate(words)
                        if w["start"] >= t0 - 0.01 and w["end"] <= t1 + 0.01]
                if not idxs:
                    continue
                rng = f"{idxs[0]}–{idxs[-1]}"
                lines.append(f'  Pokus {ti} (slova {rng}, {t1 - t0:.1f}s): "{take["text"]}"')
    return "\n".join(lines) if lines else "(žádné)"


def _parse_response(text):
    text = text.strip()
    # The model is asked for pure JSON, but be resilient if it wraps it.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r'\{[^{}]*"cut_indices"[^{}]*\}', text, re.S)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return None


def suggest_cuts(analysis, log=print, timeout_s=300):
    """Have Claude propose words to cut. Returns the number marked.

    Sets `manual=True` on each chosen word, so the existing UI re-render path
    picks them up. Existing manual choices the user already made are preserved
    where Claude agrees and replaced where it disagrees -- Claude's pass is the
    new ground truth for this call.
    """
    cli = find_claude()
    if not cli:
        raise RuntimeError("`claude` CLI nenalezeno. Nainstaluj Claude Code "
                           "(https://claude.com/claude-code).")

    words = _flat_words(analysis)
    if not words:
        return 0

    word_block = "\n".join(f"[{i}] {w['text']}" for i, w in enumerate(words))
    group_block = _build_groups_section(words, analysis)
    prompt = _PROMPT.format(words=word_block, groups=group_block)

    log(f"Posílám {len(words)} slov Claudovi (přes claude CLI)…")
    proc = subprocess.run(
        [cli, "-p", prompt],
        capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        timeout=timeout_s,
    )
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "").strip()[:400]
        raise RuntimeError(f"claude skončil chybou ({proc.returncode}): {msg}")

    data = _parse_response(proc.stdout)
    if not data or "cut_indices" not in data:
        raise RuntimeError(
            "Nelze rozluštit JSON z Claudovy odpovědi:\n" + proc.stdout[:500])

    cuts = {int(i) for i in data["cut_indices"] if isinstance(i, (int, float))}
    n = 0
    for i, w in enumerate(words):
        want_cut = i in cuts
        w["manual"] = want_cut
        w["cut"] = want_cut
        w["reason"] = "manual" if want_cut else ""
        if want_cut:
            n += 1
    log(f"Claude navrhuje vyříznout {n} slov ({n / max(len(words), 1) * 100:.0f} %).")
    return n
