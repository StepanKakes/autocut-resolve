"""MCP server that exposes AutoCut operations to Claude.

Runs in a daemon thread inside the Tk panel and serves over HTTP on localhost,
so Claude Code (in a separate terminal) can chat with you and manipulate the
analysis -- cut/keep words, pick takes, apply the cut, generate captions.

Configure Claude once:
    claude mcp add autocut --transport http http://127.0.0.1:7741/mcp

After analyzing inside the panel, just open `claude` and chat:
    "vyhoď třetí pokus z první skupiny a aplikuj střih"
    "smaž všechna 'prostě'"
    "udělej titulky velkýma písmenama, max 3 slova"
"""

import threading

from mcp.server.fastmcp import FastMCP


class AutoCutBridge:
    """Mutable state shared between the Tk UI and the MCP server thread."""

    def __init__(self):
        self.analysis = None              # dict produced by engine.analyze
        self.resolve_app = None           # captured Resolve object
        self.settings_provider = None     # callable -> current UI settings
        self.on_change_callbacks = []     # called whenever a tool mutates state
        self.log = print

    def notify(self, msg=""):
        for cb in list(self.on_change_callbacks):
            try:
                cb(msg)
            except Exception as exc:
                print(f"on_change cb error: {exc}")


def _flat_words(analysis):
    return [w for entry in analysis["clips"] for w in entry["words"]]


def _all_groups(analysis):
    return [g for entry in analysis["clips"]
            for g in (entry.get("take_groups", []) or []) if len(g) >= 2]


def build_server(bridge: AutoCutBridge):
    mcp = FastMCP("autocut", host="127.0.0.1", port=7741)

    @mcp.tool()
    def get_state() -> dict:
        """Quick summary of the current AutoCut state -- whether the user has
        analyzed a timeline, how many words, how many are marked for removal,
        how many take groups were detected."""
        if not bridge.analysis:
            return {"analyzed": False,
                    "hint": "Run analyze in the AutoCut panel first (button '1. Analyzovat')."}
        words = _flat_words(bridge.analysis)
        cut = sum(1 for w in words if w["cut"])
        groups = _all_groups(bridge.analysis)
        return {
            "analyzed": True,
            "timeline_name": bridge.analysis.get("timeline_name", "?"),
            "total_words": len(words),
            "cut_words": cut,
            "kept_words": len(words) - cut,
            "take_groups": len(groups),
        }

    @mcp.tool()
    def get_transcript(only_kept: bool = False) -> str:
        """Return the transcript. Each word is prefixed with its flat index in
        [brackets]; words currently marked for cut are wrapped in ~~strikethrough~~.
        Set only_kept=true to see what the final video will say."""
        if not bridge.analysis:
            return "No analysis yet."
        out = []
        for i, w in enumerate(_flat_words(bridge.analysis)):
            if only_kept and w["cut"]:
                continue
            if w["cut"] and not only_kept:
                out.append(f"[{i}]~~{w['text']}~~")
            else:
                out.append(f"[{i}]{w['text']}")
        return " ".join(out)

    @mcp.tool()
    def list_take_groups() -> list:
        """List detected groups of repeated take attempts. For each take you get
        its text, duration, selected flag, and the flat word-index range so you
        can target it precisely. Pick the best take per group with select_take."""
        if not bridge.analysis:
            return []
        words = _flat_words(bridge.analysis)
        result = []
        for gi, group in enumerate(_all_groups(bridge.analysis)):
            takes = []
            for ti, t in enumerate(group):
                idxs = [i for i, w in enumerate(words)
                        if w["start"] >= t["start"] - 0.01
                        and w["end"] <= t["end"] + 0.01]
                takes.append({
                    "take_index": ti,
                    "text": t["text"],
                    "duration_s": round(t["end"] - t["start"], 2),
                    "selected": t["selected"],
                    "word_indices": idxs,
                })
            result.append({"group_index": gi, "takes": takes})
        return result

    @mcp.tool()
    def cut_words(indices: list[int]) -> str:
        """Mark the given flat word indices for removal (manual cut)."""
        if not bridge.analysis:
            return "No analysis."
        words = _flat_words(bridge.analysis)
        n = 0
        for i in indices:
            if 0 <= i < len(words):
                words[i]["manual"] = True
                words[i]["cut"] = True
                words[i]["reason"] = "manual"
                n += 1
        bridge.notify("words updated")
        return f"Marked {n} word(s) for removal."

    @mcp.tool()
    def keep_words(indices: list[int]) -> str:
        """Unmark the given indices so they stay in the final video."""
        if not bridge.analysis:
            return "No analysis."
        words = _flat_words(bridge.analysis)
        n = 0
        for i in indices:
            if 0 <= i < len(words):
                words[i]["manual"] = False
                words[i]["cut"] = False
                words[i]["reason"] = ""
                n += 1
        bridge.notify("words updated")
        return f"Kept {n} word(s)."

    @mcp.tool()
    def select_take(group_index: int, take_index: int) -> str:
        """In a take group, pick which attempt to keep. The others get auto-cut."""
        if not bridge.analysis:
            return "No analysis."
        groups = _all_groups(bridge.analysis)
        if not (0 <= group_index < len(groups)):
            return f"group_index {group_index} out of range (have {len(groups)})."
        g = groups[group_index]
        if not (0 <= take_index < len(g)):
            return f"take_index {take_index} out of range in group {group_index}."
        for i, t in enumerate(g):
            t["selected"] = (i == take_index)
        bridge.notify("take selected")
        return f"Group {group_index}: now keeping take {take_index}."

    @mcp.tool()
    def apply_cut(make_captions: bool = False) -> str:
        """Build a new '<name> - AutoCut' timeline reflecting the current cut
        state. Set make_captions=true to drop a subtitle track at the same time
        (uses the existing transcript, no extra whisper pass)."""
        if not bridge.analysis:
            return "No analysis -- analyze a timeline first."
        if bridge.resolve_app is None:
            return "Resolve is not reachable from this process."
        import engine  # late import keeps the server module light
        cfg = bridge.settings_provider() if bridge.settings_provider else {}
        cfg["make_captions"] = bool(make_captions)
        try:
            engine.apply(bridge.analysis, cfg, log=bridge.log,
                         resolve_app=bridge.resolve_app)
            bridge.notify("applied")
            return "Cut applied. New timeline created and made current."
        except Exception as exc:
            return f"Apply failed: {exc}"

    @mcp.tool()
    def generate_captions() -> str:
        """Generate captions on the CURRENT Resolve timeline. Reuses the
        analysis transcript if one is loaded; otherwise transcribes fresh."""
        if bridge.resolve_app is None:
            return "Resolve unreachable."
        import engine
        import captions as captions_mod
        cfg = bridge.settings_provider() if bridge.settings_provider else {}
        try:
            if bridge.analysis:
                engine.captions_from_analysis(bridge.analysis, settings=cfg,
                                              log=bridge.log,
                                              resolve_app=bridge.resolve_app)
            else:
                cap_cfg = engine.caption_settings({**engine.DEFAULTS, **cfg})
                captions_mod.run(settings=cap_cfg, log=bridge.log,
                                 resolve_app=bridge.resolve_app)
            return "Captions added on the current timeline."
        except Exception as exc:
            return f"Captions failed: {exc}"

    @mcp.tool()
    def find_words(query: str) -> list:
        """Find flat indices of words whose text contains `query`
        (case-insensitive, accent-insensitive). Useful for 'smaž všechna prostě'."""
        if not bridge.analysis:
            return []
        import unicodedata

        def _norm(s):
            return "".join(c for c in unicodedata.normalize("NFD", s)
                           if unicodedata.category(c) != "Mn").lower()
        q = _norm(query.strip(" \t.,!?"))
        if not q:
            return []
        out = []
        for i, w in enumerate(_flat_words(bridge.analysis)):
            if q in _norm(w["text"].strip(" \t.,!?")):
                out.append(i)
        return out

    return mcp


def serve_in_thread(bridge: AutoCutBridge, host="127.0.0.1", port=7741):
    """Start the MCP server on http://127.0.0.1:7741/mcp in a daemon thread.

    FastMCP's own `run()` calls uvicorn.run -> Server.serve, which by default
    tries to install signal handlers. Signal handler registration only works on
    the *main* thread, so doing it from our worker thread raises silently and
    nothing ends up binding the port. We side-step that by building the ASGI
    app ourselves and running uvicorn with signal-handler install disabled.
    """
    import asyncio
    import uvicorn

    server = build_server(bridge)

    # Reach into FastMCP for the streamable-HTTP ASGI app. The public method
    # was added in recent mcp versions; fall back to the private one if needed.
    if hasattr(server, "streamable_http_app"):
        app = server.streamable_http_app()
    else:
        app = server._mcp_server.streamable_http_app()  # type: ignore[attr-defined]

    config = uvicorn.Config(app, host=host, port=port,
                            log_level="warning", access_log=False)
    uv_server = uvicorn.Server(config)
    # Block uvicorn from touching signals -- it would crash on a non-main thread.
    uv_server.install_signal_handlers = lambda: None  # type: ignore[assignment]

    def _run():
        try:
            asyncio.run(uv_server.serve())
        except Exception as exc:
            print(f"MCP server stopped: {exc}")

    t = threading.Thread(target=_run, daemon=True, name="autocut-mcp")
    t.start()
    return t
