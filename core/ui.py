"""AutoCut settings window (Tkinter).

Runs in-process from Resolve's Scripts menu, so the Resolve API works even in
the free version (which blocks both the Fusion UIManager and external
scripting). Heavy work runs on a worker thread; the log streams live into the
window via a thread-safe queue polled on the Tk main thread.
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


def _group_label(key):
    return f"{_GROUP_NAMES.get(key, key)}: {', '.join(FILLER_GROUPS[key][:4])}"


def run(resolve_app=None):
    root = tk.Tk()
    root.title("AutoCut")
    root.geometry("440x720")

    log_q = queue.Queue()
    running = {"flag": False}

    pad = {"padx": 10, "pady": 3}
    main = ttk.Frame(root, padding=12)
    main.pack(fill="both", expand=True)

    ttk.Label(main, text="AutoCut", font=("Helvetica", 18, "bold")).pack(anchor="w")

    # --- Silence ---
    v_sil = tk.BooleanVar(value=engine.DEFAULTS["cut_silences"])
    ttk.Checkbutton(main, text="Vyříznout ticho", variable=v_sil).pack(anchor="w", pady=(8, 0))
    sil_box = ttk.Frame(main)
    sil_box.pack(fill="x", padx=20)

    def _entry(parent, label, default):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=1)
        ttk.Label(row, text=label, width=24).pack(side="left")
        var = tk.StringVar(value=str(default))
        ttk.Entry(row, textvariable=var, width=10).pack(side="left")
        return var

    v_noise = _entry(sil_box, "Práh ticha (dB)", engine.DEFAULTS["noise_db"])
    v_minsil = _entry(sil_box, "Min. délka ticha (s)", engine.DEFAULTS["min_silence_dur"])
    v_spad = _entry(sil_box, "Ponechat kolem řeči (s)", engine.DEFAULTS["silence_pad"])

    # --- Fillers ---
    v_fill = tk.BooleanVar(value=engine.DEFAULTS["remove_fillers"])
    ttk.Checkbutton(main, text="Smazat vycpávková slova", variable=v_fill).pack(anchor="w", pady=(10, 0))
    fill_box = ttk.Frame(main)
    fill_box.pack(fill="x", padx=20)
    group_vars = {}
    for g in ("hesitation", "verbal", "phrases", "connectors"):
        gv = tk.BooleanVar(value=g in engine.DEFAULTS["filler_groups"])
        group_vars[g] = gv
        ttk.Checkbutton(fill_box, text=_group_label(g), variable=gv).pack(anchor="w")
    ttk.Label(fill_box, text="Vlastní slova (oddělená čárkou):").pack(anchor="w", pady=(4, 0))
    v_custom = tk.StringVar(value="")
    ttk.Entry(fill_box, textvariable=v_custom).pack(fill="x")

    # --- Repeats ---
    v_rep = tk.BooleanVar(value=engine.DEFAULTS["remove_repeats"])
    ttk.Checkbutton(main, text="Vyříznout opakované pokusy (nechat poslední)",
                    variable=v_rep).pack(anchor="w", pady=(10, 0))
    rep_box = ttk.Frame(main)
    rep_box.pack(fill="x", padx=20)
    v_repthr = _entry(rep_box, "Podobnost (0–1)", engine.DEFAULTS["repeat_threshold"])

    # --- Captions ---
    v_cap = tk.BooleanVar(value=engine.DEFAULTS["make_captions"])
    ttk.Checkbutton(main, text="Vytvořit titulky", variable=v_cap).pack(anchor="w", pady=(10, 0))
    cap_box = ttk.Frame(main)
    cap_box.pack(fill="x", padx=20)
    v_lang = _entry(cap_box, "Jazyk", engine.DEFAULTS["caption_language"])

    # --- Run + status + log ---
    run_btn = ttk.Button(main, text="Spustit AutoCut")
    run_btn.pack(fill="x", pady=(12, 4))
    status = ttk.Label(main, text="Připraveno.")
    status.pack(anchor="w")
    log_widget = scrolledtext.ScrolledText(main, height=12, state="disabled", wrap="word")
    log_widget.pack(fill="both", expand=True, pady=(4, 0))

    def collect_settings():
        def fnum(var, default):
            try:
                return float(var.get())
            except ValueError:
                return default
        return {
            "cut_silences": v_sil.get(),
            "noise_db": fnum(v_noise, engine.DEFAULTS["noise_db"]),
            "min_silence_dur": fnum(v_minsil, engine.DEFAULTS["min_silence_dur"]),
            "silence_pad": fnum(v_spad, engine.DEFAULTS["silence_pad"]),
            "remove_fillers": v_fill.get(),
            "filler_groups": [g for g, var in group_vars.items() if var.get()],
            "filler_words": [w.strip() for w in v_custom.get().split(",") if w.strip()],
            "remove_repeats": v_rep.get(),
            "repeat_threshold": fnum(v_repthr, engine.DEFAULTS["repeat_threshold"]),
            "make_captions": v_cap.get(),
            "caption_language": v_lang.get().strip() or "cs",
        }

    def append_log(msg):
        log_widget.configure(state="normal")
        log_widget.insert("end", msg + "\n")
        log_widget.see("end")
        log_widget.configure(state="disabled")

    def worker(settings):
        try:
            engine.run(settings, log=lambda m: log_q.put(str(m)), resolve_app=resolve_app)
            log_q.put(("__DONE__", "Hotovo ✅"))
        except Exception as exc:
            log_q.put(("__DONE__", f"Chyba ❌: {exc}"))

    def poll():
        try:
            while True:
                item = log_q.get_nowait()
                if isinstance(item, tuple) and item[0] == "__DONE__":
                    status.configure(text=item[1])
                    running["flag"] = False
                    run_btn.configure(state="normal")
                else:
                    append_log(item)
        except queue.Empty:
            pass
        root.after(120, poll)

    def on_run():
        if running["flag"]:
            return
        running["flag"] = True
        run_btn.configure(state="disabled")
        status.configure(text="Běží… (průběh níže)")
        log_widget.configure(state="normal")
        log_widget.delete("1.0", "end")
        log_widget.configure(state="disabled")
        threading.Thread(target=worker, args=(collect_settings(),), daemon=True).start()

    run_btn.configure(command=on_run)
    root.after(120, poll)
    root.mainloop()
