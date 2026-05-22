"""Connect to a running DaVinci Resolve instance.

Three ways to get the Resolve app object, tried in order:
  1. An app object handed in by the launcher (Resolve injects `resolve`/`bmd`
     into the namespace of scripts run from Workspace > Scripts).
  2. `import fusionscript` / `DaVinciResolveScript` -- works inside Resolve.
  3. External terminal: point env vars at the bundled scripting library.
"""

import os
import sys

# Default macOS install locations (Resolve 18-20).
_API = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
_LIB = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"


def _from_module():
    """Try the bundled scripting module (works in-app and, with env, externally)."""
    try:
        import fusionscript as mod
    except ImportError:
        try:
            import DaVinciResolveScript as mod
        except ImportError:
            modules = os.path.join(_API, "Modules")
            if modules not in sys.path:
                sys.path.append(modules)
            os.environ.setdefault("RESOLVE_SCRIPT_API", _API)
            os.environ.setdefault("RESOLVE_SCRIPT_LIB", _LIB)
            try:
                import DaVinciResolveScript as mod
            except ImportError:
                return None
    try:
        return mod.scriptapp("Resolve")
    except Exception:
        return None


def get_resolve(app=None):
    """Return the Resolve scripting app object, or raise with a clear message.

    `app` may be a pre-resolved Resolve object (or a `bmd`-like module exposing
    `scriptapp`) captured by the launcher from the injected globals.
    """
    if app is not None:
        if hasattr(app, "GetProjectManager"):
            return app
        if hasattr(app, "scriptapp"):  # a bmd-like module
            got = app.scriptapp("Resolve")
            if got is not None:
                return got

    resolve = _from_module()
    if resolve is None:
        raise RuntimeError(
            "Resolve scripting app not reachable. Run this from inside Resolve "
            "(Workspace > Scripts), and check Preferences > System > General > "
            "external scripting is set to Local."
        )
    return resolve


def _get_bmd_module():
    """Return the fusionscript/bmd module that exposes UIDispatcher/scriptapp."""
    try:
        import fusionscript as fs
        if hasattr(fs, "UIDispatcher"):
            return fs
    except ImportError:
        pass
    import __main__  # last resort: injected global
    return getattr(__main__, "bmd", None)


def get_ui(resolve, log=print):
    """Return (UIManager, UIDispatcher) for building native windows.

    Resolve injects `fusion`/`bmd` globals only for Lua scripts, not Python, and
    different builds expose UIManager via different objects -- so we try every
    known source and use the first that yields a non-None UIManager.
    """
    bmd = _get_bmd_module()
    if bmd is None or not hasattr(bmd, "UIDispatcher"):
        raise RuntimeError("UIDispatcher unavailable; cannot build the UI window.")

    import __main__
    candidates = []
    if hasattr(bmd, "scriptapp"):
        candidates.append(("bmd.scriptapp('Fusion')", lambda: bmd.scriptapp("Fusion")))
    candidates.append(("resolve.Fusion()", lambda: resolve.Fusion()))
    candidates.append(("__main__.fusion", lambda: getattr(__main__, "fusion", None)))
    candidates.append(("__main__.fu", lambda: getattr(__main__, "fu", None)))

    for label, getter in candidates:
        try:
            fusion = getter()
        except Exception as exc:
            log(f"  UI source {label}: error {exc}")
            continue
        ui = getattr(fusion, "UIManager", None) if fusion is not None else None
        log(f"  UI source {label}: fusion={'ok' if fusion else 'None'}, "
            f"UIManager={'ok' if ui else 'None'}")
        if ui is not None:
            return ui, bmd.UIDispatcher(ui)

    # Some builds expose UIManager directly on the bmd module.
    ui = getattr(bmd, "UIManager", None)
    if ui is not None:
        log("  UI source bmd.UIManager: ok")
        return ui, bmd.UIDispatcher(ui)

    raise RuntimeError("No UIManager available from any source (native UI blocked in this build).")


def get_context(app=None):
    """Return (resolve, project, media_pool, timeline). Raises if anything is missing."""
    resolve = get_resolve(app)
    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject()
    if project is None:
        raise RuntimeError("No project is open in Resolve.")
    media_pool = project.GetMediaPool()
    timeline = project.GetCurrentTimeline()
    if timeline is None:
        raise RuntimeError("No timeline is open. Open the timeline you want to cut.")
    return resolve, project, media_pool, timeline
