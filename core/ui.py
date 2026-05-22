"""AutoCut window (Tkinter) with an interactive, Descript-style transcript.

Flow: Analyze -> review the transcript (words proposed for removal are struck
through and colour-coded; click any word to keep/cut it) -> Apply, which
rebuilds the timeline from the final selection. Runs in-process from Resolve's
Scripts menu (the free version blocks the Fusion UIManager and external
scripting, but in-process Tkinter works).
"""

import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import engine                       # noqa: E402
from fillers import FILLER_GROUPS   # noqa: E402

_GROUP_NAMES = {
    "hesitation": "Hezitace", "verbal": "Slovní vata",
    "phrases": "Fráze", "connectors": "Spojky-vata",
}

LANGUAGES = [
    ("🇨🇿 Čeština", "cs"), ("🇸🇰 Slovenčina", "sk"), ("🇬🇧 English", "en"),
    ("🇩🇪 Deutsch", "de"), ("🇪🇸 Español", "es"), ("🇫🇷 Français", "fr"),
    ("🇮🇹 Italiano", "it"), ("🇵🇱 Polski", "pl"), ("🇵🇹 Português", "pt"),
    ("🇷🇺 Русский", "ru"), ("🇺🇦 Українська", "uk"), ("🇳🇱 Nederlands", "nl"),
]

REASON_COLOR = {"filler": "#d9822b", "repeat": "#5c6bc0", "manual": "#e53935"}


def _group_label(key):
    return f"{_GROUP_NAMES.get(key, key)}: {', '.join(FILLER_GROUPS[key][:4])}"


def run(resolve_app=None):
    root = tk.Tk()
    root.title("AutoCut")
    root.geometry("560x820")

    log_q = queue.Queue()
    state = {"analysis": None, "busy": False, "words_flat": [],
             "preview_tl": None, "live_dirty": False, "after_id": None}

    main = ttk.Frame(root, padding=12)
    main.pack(fill="both", expand=True)
    ttk.Label(main, text="AutoCut", font=("Helvetica", 18, "bold")).pack(anchor="w")

    # ---- options grid ----
    opt = ttk.Frame(main)
    opt.pack(fill="x", pady=(6, 4))

    def spin(parent, label, default, frm, to, inc, r, c):
        ttk.Label(parent, text=label).grid(row=r, column=c, sticky="w", padx=(0, 4), pady=2)
        var = tk.StringVar(value=str(default))
        tk.Spinbox(parent, from_=frm, to=to, increment=inc, textvariable=var,
                   width=7).grid(row=r, column=c + 1, sticky="w", pady=2)
        return var

    # language
    ttk.Label(opt, text="Jazyk").grid(row=0, column=0, sticky="w", pady=2)
    lang_cb = ttk.Combobox(opt, values=[d for d, _ in LANGUAGES], state="readonly", width=16)
    lang_cb.current(0)
    lang_cb.grid(row=0, column=1, columnspan=3, sticky="w", pady=2)

    v_sil = tk.BooleanVar(value=engine.DEFAULTS["cut_silences"])
    ttk.Checkbutton(opt, text="Vyříznout ticho", variable=v_sil).grid(
        row=1, column=0, columnspan=2, sticky="w")
    v_noise = spin(opt, "Práh (dB)", engine.DEFAULTS["noise_db"], -90, 0, 1, 1, 2)
    v_minsil = spin(opt, "Min. ticho (s)", engine.DEFAULTS["min_silence_dur"], 0, 5, 0.05, 2, 2)
    v_spad = spin(opt, "Rezerva (s)", engine.DEFAULTS["silence_pad"], 0, 1, 0.01, 3, 2)

    ttk.Label(opt, text="Vycpávková slova:").grid(
        row=2, column=0, columnspan=2, sticky="w")
    v_rep = tk.BooleanVar(value=engine.DEFAULTS["remove_repeats"])
    ttk.Checkbutton(opt, text="Opakované pokusy", variable=v_rep).grid(
        row=3, column=0, columnspan=2, sticky="w")
    v_repthr = spin(opt, "Podobnost", engine.DEFAULTS["repeat_threshold"], 0, 1, 0.05, 4, 2)

    # filler groups
    fill_box = ttk.Frame(main)
    fill_box.pack(fill="x")
    group_vars = {}
    for g in ("hesitation", "verbal", "phrases", "connectors"):
        gv = tk.BooleanVar(value=g in engine.DEFAULTS["filler_groups"])
        group_vars[g] = gv
        ttk.Checkbutton(fill_box, text=_group_label(g), variable=gv).pack(anchor="w")
    custom_row = ttk.Frame(main)
    custom_row.pack(fill="x", pady=(2, 6))
    ttk.Label(custom_row, text="Vlastní slova:").pack(side="left")
    v_custom = tk.StringVar(value="")
    ttk.Entry(custom_row, textvariable=v_custom).pack(side="left", fill="x", expand=True, padx=4)

    # ---- actions ----
    act = ttk.Frame(main)
    act.pack(fill="x")
    analyze_btn = ttk.Button(act, text="1. Analyzovat")
    analyze_btn.pack(side="left")
    v_live = tk.BooleanVar(value=False)
    live_cb = ttk.Checkbutton(act, text="Živě", variable=v_live)
    live_cb.pack(side="left", padx=8)
    v_cap = tk.BooleanVar(value=engine.DEFAULTS["make_captions"])
    ttk.Checkbutton(act, text="Titulky", variable=v_cap).pack(side="left")
    apply_btn = ttk.Button(act, text="2. Aplikovat střih", state="disabled")
    apply_btn.pack(side="right")

    status = ttk.Label(main, text="Připraveno. Klikni Analyzovat.")
    status.pack(anchor="w", pady=(4, 2))

    ttk.Label(main, text="Přepis (klikni na slovo = smazat/ponechat):",
              font=("Helvetica", 10, "bold")).pack(anchor="w")
    txt = scrolledtext.ScrolledText(main, height=14, wrap="word",
                                    font=("Helvetica", 13), state="disabled")
    txt.pack(fill="both", expand=True, pady=(2, 4))

    log_widget = scrolledtext.ScrolledText(main, height=5, state="disabled", wrap="word")
    log_widget.pack(fill="x")

    # ---- helpers ----
    def selected_lang():
        return dict(LANGUAGES)[lang_cb.get()] if lang_cb.get() else "cs"

    def fnum(var, default):
        try:
            return float(var.get())
        except ValueError:
            return default

    def collect_settings():
        groups = [g for g, var in group_vars.items() if var.get()]
        custom = [w.strip() for w in v_custom.get().split(",") if w.strip()]
        return {
            "cut_silences": v_sil.get(),
            "noise_db": fnum(v_noise, engine.DEFAULTS["noise_db"]),
            "min_silence_dur": fnum(v_minsil, engine.DEFAULTS["min_silence_dur"]),
            "silence_pad": fnum(v_spad, engine.DEFAULTS["silence_pad"]),
            "remove_fillers": bool(groups or custom),  # on if anything is selected
            "filler_groups": groups,
            "filler_words": custom,
            "remove_repeats": v_rep.get(),
            "repeat_threshold": fnum(v_repthr, engine.DEFAULTS["repeat_threshold"]),
            "make_captions": v_cap.get(),
            "caption_language": selected_lang(),
        }

    def log(msg):
        log_widget.configure(state="normal")
        log_widget.insert("end", str(msg) + "\n")
        log_widget.see("end")
        log_widget.configure(state="disabled")

    def style_word(idx):
        w = state["words_flat"][idx]
        tag = f"w{idx}"
        if w["cut"]:
            color = REASON_COLOR.get(w["reason"] or "manual", "#e53935")
            txt.tag_configure(tag, overstrike=True, foreground=color)
        else:
            txt.tag_configure(tag, overstrike=False, foreground="")

    def update_summary():
        words = state["words_flat"]
        cut = [w for w in words if w["cut"]]
        secs = sum(w["end"] - w["start"] for w in cut)
        by = {}
        for w in cut:
            by[w["reason"] or "manual"] = by.get(w["reason"] or "manual", 0) + 1
        parts = ", ".join(f"{k}: {v}" for k, v in by.items()) or "nic"
        status.configure(text=f"Ke smazání: {len(cut)} slov (~{secs:.1f}s) — {parts}")

    def toggle(idx):
        w = state["words_flat"][idx]
        if w["cut"]:
            w["cut"], w["reason"] = False, ""
        else:
            w["cut"], w["reason"] = True, (w["reason"] or "manual")
        style_word(idx)
        update_summary()
        if v_live.get():
            schedule_live()

    def render_transcript(analysis):
        state["words_flat"] = [w for entry in analysis["clips"] for w in entry["words"]]
        txt.configure(state="normal")
        txt.delete("1.0", "end")
        for idx, w in enumerate(state["words_flat"]):
            tag = f"w{idx}"
            txt.insert("end", w["text"] + " ", (tag,))
            style_word(idx)
            txt.tag_bind(tag, "<Button-1>", lambda e, i=idx: toggle(i))
            txt.tag_bind(tag, "<Enter>", lambda e: txt.configure(cursor="hand2"))
            txt.tag_bind(tag, "<Leave>", lambda e: txt.configure(cursor=""))
        txt.configure(state="disabled")
        update_summary()

    # ---- threaded actions ----
    def set_busy(b):
        state["busy"] = b
        analyze_btn.configure(state="disabled" if b else "normal")
        apply_btn.configure(state="disabled" if (b or not state["analysis"]) else "normal")

    def on_analyze():
        if state["busy"]:
            return
        set_busy(True)
        status.configure(text="Analyzuji… (průběh v logu)")
        log_widget.configure(state="normal"); log_widget.delete("1.0", "end")
        log_widget.configure(state="disabled")
        settings = collect_settings()

        def worker():
            try:
                a = engine.analyze(settings, log=lambda m: log_q.put(str(m)),
                                   resolve_app=resolve_app)
                log_q.put(("__ANALYSIS__", a))
            except Exception as exc:
                log_q.put(("__ERR__", str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def start_apply(live):
        if state["busy"] or not state["analysis"]:
            return
        set_busy(True)
        status.configure(text="Aplikuji živě…" if live else "Aplikuji střih…")
        settings = collect_settings()
        if live:
            settings = dict(settings, make_captions=False)  # captions only on manual apply
        analysis = state["analysis"]
        prev = state["preview_tl"]

        def worker():
            try:
                tl = engine.apply(analysis, settings, log=lambda m: log_q.put(str(m)),
                                  resolve_app=resolve_app, replace_timeline=prev)
                log_q.put(("__APPLIED__", tl))
            except Exception as exc:
                log_q.put(("__ERR__", str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def schedule_live():
        if state["after_id"]:
            root.after_cancel(state["after_id"])
        state["after_id"] = root.after(700, trigger_live)

    def trigger_live():
        state["after_id"] = None
        if state["busy"]:
            state["live_dirty"] = True   # rebuild again once the current one finishes
        else:
            start_apply(live=True)

    def on_live_toggle():
        if v_live.get() and state["analysis"]:
            schedule_live()

    def poll():
        try:
            while True:
                item = log_q.get_nowait()
                if isinstance(item, tuple):
                    kind, val = item
                    if kind == "__ANALYSIS__":
                        state["analysis"] = val
                        render_transcript(val)
                        set_busy(False)
                    elif kind == "__APPLIED__":
                        state["preview_tl"] = val
                        set_busy(False)
                        status.configure(text="Hotovo ✅")
                        if state["live_dirty"]:
                            state["live_dirty"] = False
                            schedule_live()
                    elif kind == "__ERR__":
                        set_busy(False)
                        status.configure(text=f"Chyba ❌: {val}")
                        log(f"CHYBA: {val}")
                else:
                    log(item)
        except queue.Empty:
            pass
        root.after(120, poll)

    analyze_btn.configure(command=on_analyze)
    apply_btn.configure(command=lambda: start_apply(live=False))
    live_cb.configure(command=on_live_toggle)
    root.after(120, poll)
    root.mainloop()
