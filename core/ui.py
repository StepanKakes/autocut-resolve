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
import captions                     # noqa: E402
import transcribe                   # noqa: E402
from fillers import FILLER_GROUPS   # noqa: E402

CASE_OPTIONS = [("beze změny", "asis"), ("První velké (věta)", "sentence"),
                ("VŠE VELKÝM", "upper"), ("vše malé", "lower")]

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

PALETTE = {
    "bg": "#15171c", "panel": "#1e2128", "field": "#2a2e37",
    "fg": "#e9ebef", "muted": "#9aa0aa", "accent": "#4f8cff",
    "accent_active": "#3b78f0", "border": "#363b45",
}

REASON_COLOR = {"filler": "#f0a040", "take": "#6ea8fe", "manual": "#ff6b6b"}


def _group_label(key):
    return f"{_GROUP_NAMES.get(key, key)}: {', '.join(FILLER_GROUPS[key][:4])}"


def _apply_theme(root):
    p = PALETTE
    style = ttk.Style(root)
    try:
        style.theme_use("clam")  # only theme that fully honours colours on macOS
    except tk.TclError:
        pass
    root.configure(bg=p["bg"])
    root.option_add("*TCombobox*Listbox.background", p["field"])
    root.option_add("*TCombobox*Listbox.foreground", p["fg"])
    root.option_add("*TCombobox*Listbox.selectBackground", p["accent"])

    style.configure(".", background=p["bg"], foreground=p["fg"],
                    fieldbackground=p["field"], bordercolor=p["border"],
                    lightcolor=p["field"], darkcolor=p["field"],
                    troughcolor=p["field"], font=("Helvetica", 12))
    style.configure("TFrame", background=p["bg"])
    style.configure("TLabel", background=p["bg"], foreground=p["fg"])
    style.configure("Muted.TLabel", background=p["bg"], foreground=p["muted"])
    style.configure("Title.TLabel", background=p["bg"], foreground=p["fg"],
                    font=("Helvetica", 22, "bold"))
    style.configure("Status.TLabel", background=p["bg"], foreground=p["muted"],
                    font=("Helvetica", 12, "bold"))
    style.configure("TCheckbutton", background=p["bg"], foreground=p["fg"],
                    focuscolor=p["bg"], padding=2)
    style.map("TCheckbutton",
              background=[("active", p["bg"])],
              foreground=[("active", p["fg"]), ("disabled", p["muted"])])
    style.configure("TButton", background=p["field"], foreground=p["fg"],
                    bordercolor=p["border"], focuscolor=p["bg"], padding=7, relief="flat")
    style.map("TButton", background=[("active", p["border"]), ("disabled", p["panel"])],
              foreground=[("disabled", p["muted"])])
    style.configure("Accent.TButton", background=p["accent"], foreground="#ffffff",
                    padding=7, relief="flat", font=("Helvetica", 12, "bold"))
    style.map("Accent.TButton",
              background=[("active", p["accent_active"]), ("disabled", p["panel"])],
              foreground=[("disabled", p["muted"])])
    style.configure("TCombobox", fieldbackground=p["field"], background=p["field"],
                    foreground=p["fg"], arrowcolor=p["fg"], bordercolor=p["border"],
                    padding=4)
    style.map("TCombobox", fieldbackground=[("readonly", p["field"])])
    style.configure("TEntry", fieldbackground=p["field"], foreground=p["fg"],
                    insertcolor=p["fg"], bordercolor=p["border"], padding=4)
    return style


def run(resolve_app=None):
    root = tk.Tk()
    root.title("AutoCut")
    root.geometry("600x860")
    _apply_theme(root)
    p = PALETTE

    log_q = queue.Queue()
    state = {"analysis": None, "busy": False, "words_flat": [],
             "preview_tl": None, "live_dirty": False, "after_id": None,
             "cancel": None}

    main = ttk.Frame(root, padding=16)
    main.pack(fill="both", expand=True)
    header = ttk.Frame(main)
    header.pack(fill="x")
    ttk.Label(header, text="AutoCut", style="Title.TLabel").pack(side="left")
    ttk.Label(header, text="přepis → klikni → střih", style="Muted.TLabel").pack(
        side="left", padx=10, pady=(10, 0))
    ttk.Separator(main, orient="horizontal").pack(fill="x", pady=(8, 8))

    # ---- options grid ----
    opt = ttk.Frame(main)
    opt.pack(fill="x", pady=(6, 4))

    def spin(parent, label, default, frm, to, inc, r, c):
        ttk.Label(parent, text=label).grid(row=r, column=c, sticky="w", padx=(0, 4), pady=3)
        var = tk.StringVar(value=str(default))
        tk.Spinbox(parent, from_=frm, to=to, increment=inc, textvariable=var, width=7,
                   bg=p["field"], fg=p["fg"], buttonbackground=p["field"],
                   insertbackground=p["fg"], relief="flat", highlightthickness=1,
                   highlightbackground=p["border"], highlightcolor=p["accent"],
                   readonlybackground=p["field"]).grid(
            row=r, column=c + 1, sticky="w", pady=3)
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

    # --- Captions settings ---
    ttk.Separator(main, orient="horizontal").pack(fill="x", pady=(8, 6))
    ttk.Label(main, text="Titulky", font=("Helvetica", 13, "bold")).pack(anchor="w")
    cap_row = ttk.Frame(main)
    cap_row.pack(fill="x", pady=(2, 4))
    ttk.Label(cap_row, text="Slov na titulek").pack(side="left")
    v_capwords = tk.StringVar(value="0")
    tk.Spinbox(cap_row, from_=0, to=20, textvariable=v_capwords, width=4,
               bg=p["field"], fg=p["fg"], buttonbackground=p["field"],
               insertbackground=p["fg"], relief="flat", highlightthickness=1,
               highlightbackground=p["border"]).pack(side="left", padx=(4, 14))
    v_punct = tk.BooleanVar(value=True)
    ttk.Checkbutton(cap_row, text="Interpunkce", variable=v_punct).pack(side="left", padx=(0, 14))
    ttk.Label(cap_row, text="Písmena").pack(side="left", padx=(0, 4))
    v_case = ttk.Combobox(cap_row, values=[d for d, _ in CASE_OPTIONS],
                          state="readonly", width=18)
    v_case.current(0)
    v_case.pack(side="left")
    gen_cap_btn = ttk.Button(main, text="Vygenerovat titulky (na aktuální timeline)")
    gen_cap_btn.pack(fill="x", pady=(2, 6))

    takes_btn = ttk.Button(main, text="🎬 Vybrat nejlepší pokus ze skupin", state="disabled")
    takes_btn.pack(fill="x", pady=(0, 0))

    # ---- actions ----
    act = ttk.Frame(main)
    act.pack(fill="x")
    analyze_btn = ttk.Button(act, text="1. Analyzovat", style="Accent.TButton")
    analyze_btn.pack(side="left")
    v_live = tk.BooleanVar(value=False)
    live_cb = ttk.Checkbutton(act, text="Živě", variable=v_live)
    live_cb.pack(side="left", padx=10)
    v_cap = tk.BooleanVar(value=engine.DEFAULTS["make_captions"])
    ttk.Checkbutton(act, text="Titulky", variable=v_cap).pack(side="left")
    apply_btn = ttk.Button(act, text="2. Aplikovat střih", style="Accent.TButton",
                           state="disabled")
    apply_btn.pack(side="right")

    status = ttk.Label(main, text="Připraveno. Klikni Analyzovat.", style="Status.TLabel")
    status.pack(anchor="w", pady=(10, 4))

    # transcript header + colour legend
    thead = ttk.Frame(main)
    thead.pack(fill="x")
    ttk.Label(thead, text="Přepis", font=("Helvetica", 13, "bold")).pack(side="left")
    ttk.Label(thead, text="(klikni na slovo = smazat / ponechat)",
              style="Muted.TLabel").pack(side="left", padx=6)
    legend = ttk.Frame(main)
    legend.pack(fill="x", pady=(2, 4))
    for label, key in (("vata", "filler"), ("jiný pokus", "take"), ("ručně", "manual")):
        tk.Label(legend, text=f"■ {label}", fg=REASON_COLOR[key], bg=p["bg"],
                 font=("Helvetica", 11)).pack(side="left", padx=(0, 12))

    txt = scrolledtext.ScrolledText(
        main, height=15, wrap="word", font=("Helvetica", 14),
        state="disabled", bg=p["panel"], fg=p["fg"], insertbackground=p["accent"],
        selectbackground=p["accent"], relief="flat", borderwidth=0,
        padx=12, pady=10, spacing1=2, spacing3=4,
        highlightthickness=1, highlightbackground=p["border"])
    txt.pack(fill="both", expand=True, pady=(0, 6))

    log_widget = scrolledtext.ScrolledText(
        main, height=5, state="disabled", wrap="word", font=("Menlo", 10),
        bg="#101216", fg=p["muted"], relief="flat", borderwidth=0,
        padx=8, pady=6, highlightthickness=1, highlightbackground=p["border"])
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
            "caption_max_len": engine.DEFAULTS["caption_max_len"],
            "caption_max_words": int(fnum(v_capwords, 0)),
            "caption_keep_punct": v_punct.get(),
            "caption_case": dict(CASE_OPTIONS)[v_case.get()],
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
        new = not w["cut"]
        w["manual"] = new          # remember user's choice across filter changes
        w["cut"] = new
        w["reason"] = "manual" if new else ""
        style_word(idx)
        update_summary()
        if v_live.get():
            schedule_live()

    def restyle_all():
        for idx in range(len(state["words_flat"])):
            style_word(idx)

    def open_takes_window():
        if not state["analysis"]:
            return
        # Collect all take groups across clips and only show real groups (2+ takes).
        groups = []
        for entry in state["analysis"]["clips"]:
            groups.extend(entry.get("take_groups", []) or [])
        groups = [g for g in groups if len(g) >= 2]

        win = tk.Toplevel(root)
        win.title("Skupiny pokusů — vyber nejlepší")
        win.geometry("760x560")
        win.configure(bg=p["bg"])

        ttk.Label(win, text="Vyber pokus, který chceš ponechat",
                  style="Title.TLabel", padding=(12, 10, 12, 4)).pack(anchor="w")
        ttk.Label(win, text="Ostatní pokusy ve skupině se v přepisu označí jako "
                            "‚jiný pokus‘ (modré, přeškrtnuté) a vystřihnou se.",
                  style="Muted.TLabel", padding=(12, 0, 12, 8)).pack(anchor="w")

        if not groups:
            ttk.Label(win, text="Žádné skupiny pokusů nenalezeny.",
                      padding=12).pack(anchor="w")
            ttk.Button(win, text="Zavřít", command=win.destroy).pack(pady=12)
            return

        # Scrollable area.
        outer = ttk.Frame(win)
        outer.pack(fill="both", expand=True, padx=12)
        canvas = tk.Canvas(outer, bg=p["bg"], highlightthickness=0)
        sb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>",
                   lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # Build one block per group.
        for gi, group in enumerate(groups, 1):
            frm = ttk.LabelFrame(inner, text=f"Skupina {gi}  ·  {len(group)} pokusů",
                                 padding=8)
            frm.pack(fill="x", pady=6, padx=4)
            cur = next((i for i, t in enumerate(group) if t["selected"]), len(group) - 1)
            var = tk.IntVar(value=cur)

            def make_handler(grp, v):
                def _on_change():
                    sel = v.get()
                    for i, t in enumerate(grp):
                        t["selected"] = (i == sel)
                    on_filter_change()  # recompute cuts + restyle + live
                return _on_change

            handler = make_handler(group, var)
            for i, take in enumerate(group):
                dur = take["end"] - take["start"]
                txt = take["text"].strip()
                if len(txt) > 110:
                    txt = txt[:110] + "…"
                ttk.Radiobutton(frm, text=f"Pokus {i + 1}  ·  {dur:0.1f}s\n{txt}",
                                variable=var, value=i, command=handler).pack(anchor="w", pady=2)

        ttk.Button(win, text="Zavřít", command=win.destroy).pack(pady=10)

    def on_filter_change(*_):
        # Filler/repeat options changed -> recompute marks instantly (no whisper).
        if not state["analysis"] or state["busy"]:
            return
        engine.redetect(state["analysis"], collect_settings())
        restyle_all()
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
    def set_busy(b, analyzing=False):
        state["busy"] = b
        if analyzing:
            analyze_btn.configure(state="normal", text="⏹ Zastavit",
                                  style="TButton", command=on_stop)
        else:
            analyze_btn.configure(state="disabled" if b else "normal",
                                  text="1. Analyzovat", style="Accent.TButton",
                                  command=on_analyze)
        apply_btn.configure(state="disabled" if (b or not state["analysis"]) else "normal")
        gen_cap_btn.configure(state="disabled" if b else "normal")
        takes_btn.configure(state="disabled" if (b or not state["analysis"]) else "normal")

    def on_stop():
        if state["cancel"]:
            state["cancel"].set()
        status.configure(text="Zastavuji…")

    def on_analyze():
        if state["busy"]:
            return
        state["cancel"] = threading.Event()
        set_busy(True, analyzing=True)
        status.configure(text="Analyzuji… (lze zastavit)")
        log_widget.configure(state="normal"); log_widget.delete("1.0", "end")
        log_widget.configure(state="disabled")
        settings = collect_settings()
        cancel = state["cancel"]

        def worker():
            try:
                a = engine.analyze(settings, log=lambda m: log_q.put(str(m)),
                                   resolve_app=resolve_app, cancel=cancel)
                log_q.put(("__ANALYSIS__", a))
            except transcribe.Cancelled:
                log_q.put(("__DONE__", "Zastaveno ⏹"))
            except Exception as exc:
                log_q.put(("__ERR__", str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def on_gen_captions():
        if state["busy"]:
            return
        set_busy(True)
        settings = collect_settings()
        analysis = state["analysis"]
        reuse = analysis is not None
        status.configure(text="Generuji titulky (z přepisu)…" if reuse
                         else "Generuji titulky (přepisuji)…")

        def worker():
            try:
                if reuse:  # use the transcript we already have -- no second whisper pass
                    engine.captions_from_analysis(analysis, settings=settings,
                                                  log=lambda m: log_q.put(str(m)),
                                                  resolve_app=resolve_app)
                else:
                    cap_cfg = engine.caption_settings(dict(engine.DEFAULTS, **settings))
                    captions.run(settings=cap_cfg, log=lambda m: log_q.put(str(m)),
                                 resolve_app=resolve_app)
                log_q.put(("__DONE__", "Titulky hotové ✅"))
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
                    elif kind == "__DONE__":
                        set_busy(False)
                        status.configure(text=val)
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
    gen_cap_btn.configure(command=on_gen_captions)
    takes_btn.configure(command=open_takes_window)
    live_cb.configure(command=on_live_toggle)

    # Recompute the transcript marks live whenever a filter option changes.
    for _v in list(group_vars.values()) + [v_rep, v_repthr, v_custom, v_sil]:
        _v.trace_add("write", on_filter_change)

    root.after(120, poll)
    root.mainloop()
