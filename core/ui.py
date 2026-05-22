"""Native AutoCut settings window, built with Resolve's UIManager.

The launcher passes in `fusion` (provides UIManager) and `bmd` (provides
UIDispatcher), which Resolve injects into scripts run from Workspace > Scripts.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import engine                                  # noqa: E402
from fillers import FILLER_GROUPS              # noqa: E402
from resolve_connect import get_resolve, get_ui  # noqa: E402


def _group_label(key):
    examples = ", ".join(FILLER_GROUPS[key][:4])
    names = {
        "hesitation": "Hezitace", "verbal": "Slovní vata",
        "phrases": "Fráze", "connectors": "Spojky-vata",
    }
    return f"{names.get(key, key)}: {examples}"


def run(resolve_app=None):
    print("ui.run: connecting to Resolve...")
    resolve = get_resolve(resolve_app)
    print("ui.run: getting Fusion UIManager / dispatcher...")
    ui, disp = get_ui(resolve, log=print)
    print("ui.run: building window...")

    def row(label, ed_id, value, hint=""):
        return ui.HGroup({"Weight": 0}, [
            ui.Label({"Text": label, "MinimumSize": [160, 0]}),
            ui.LineEdit({"ID": ed_id, "Text": str(value), "PlaceholderText": hint}),
        ])

    win = disp.AddWindow({
        "ID": "AutoCutWin",
        "WindowTitle": "AutoCut",
        "Geometry": [200, 150, 480, 720],
    }, [
        ui.VGroup([
            ui.Label({"Text": "AutoCut", "Weight": 0,
                      "StyleSheet": "font-size: 20px; font-weight: bold;"}),

            ui.CheckBox({"ID": "cb_sil", "Text": "Vyříznout ticho", "Checked": True, "Weight": 0}),
            row("  Práh ticha (dB)", "noise", engine.DEFAULTS["noise_db"], "-30"),
            row("  Min. délka ticha (s)", "minsil", engine.DEFAULTS["min_silence_dur"], "0.5"),
            row("  Ponechat kolem řeči (s)", "spad", engine.DEFAULTS["silence_pad"], "0.10"),

            ui.VGap(8),
            ui.CheckBox({"ID": "cb_fill", "Text": "Smazat vycpávková slova", "Checked": False, "Weight": 0}),
            ui.CheckBox({"ID": "g_hesitation", "Text": _group_label("hesitation"), "Checked": True, "Weight": 0}),
            ui.CheckBox({"ID": "g_verbal", "Text": _group_label("verbal"), "Checked": True, "Weight": 0}),
            ui.CheckBox({"ID": "g_phrases", "Text": _group_label("phrases"), "Checked": False, "Weight": 0}),
            ui.CheckBox({"ID": "g_connectors", "Text": _group_label("connectors"), "Checked": False, "Weight": 0}),
            ui.LineEdit({"ID": "custom", "PlaceholderText": "vlastní slova oddělená čárkou", "Weight": 0}),

            ui.VGap(8),
            ui.CheckBox({"ID": "cb_cap", "Text": "Vytvořit titulky", "Checked": False, "Weight": 0}),
            row("  Jazyk", "lang", engine.DEFAULTS["caption_language"], "cs"),

            ui.VGap(8),
            ui.Button({"ID": "RunBtn", "Text": "Spustit AutoCut", "Weight": 0}),
            ui.Label({"ID": "Status", "Text": "Připraveno.", "Weight": 0}),
            ui.TextEdit({"ID": "Log", "ReadOnly": True, "Text": ""}),
        ]),
    ])

    itm = win.GetItems()

    def fnum(ed_id, default):
        try:
            return float(itm[ed_id].Text)
        except (ValueError, AttributeError):
            return default

    def ui_log(msg):
        print(msg)
        try:
            itm["Log"].PlainText = (itm["Log"].PlainText + str(msg) + "\n")
        except Exception:
            pass

    def collect_settings():
        groups = [g for g in ("hesitation", "verbal", "phrases", "connectors")
                  if itm[f"g_{g}"].Checked]
        custom = [w.strip() for w in itm["custom"].Text.split(",") if w.strip()]
        return {
            "cut_silences": itm["cb_sil"].Checked,
            "noise_db": fnum("noise", engine.DEFAULTS["noise_db"]),
            "min_silence_dur": fnum("minsil", engine.DEFAULTS["min_silence_dur"]),
            "silence_pad": fnum("spad", engine.DEFAULTS["silence_pad"]),
            "remove_fillers": itm["cb_fill"].Checked,
            "filler_groups": groups,
            "filler_words": custom,
            "make_captions": itm["cb_cap"].Checked,
            "caption_language": itm["lang"].Text.strip() or "cs",
        }

    def on_run(ev):
        itm["Status"].Text = "Běží… (průběh sleduj v konzoli)"
        itm["Log"].PlainText = ""
        try:
            engine.run(collect_settings(), log=ui_log, resolve_app=resolve_app)
            itm["Status"].Text = "Hotovo ✅"
        except Exception as exc:
            ui_log(f"CHYBA: {exc}")
            itm["Status"].Text = "Chyba ❌ (viz log)"

    def on_close(ev):
        disp.ExitLoop()

    win.On.AutoCutWin.Close = on_close
    win.On.RunBtn.Clicked = on_run

    print("ui.run: window built, showing it now.")
    win.Show()
    disp.RunLoop()
    win.Hide()
    print("ui.run: window closed.")
