"""Connect to a running DaVinci Resolve instance.

Works both when launched from inside Resolve (Workspace > Scripts), where the
DaVinciResolveScript module is already importable, and from an external terminal,
where we have to point PYTHONPATH/env vars at the bundled scripting library.
"""

import os
import sys

# Default macOS install locations (Resolve 18-20).
_API = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
_LIB = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"


def get_resolve():
    """Return the Resolve scripting app object, or raise with a clear message."""
    try:
        import DaVinciResolveScript as dvr  # available when run inside Resolve
    except ImportError:
        modules = os.path.join(_API, "Modules")
        if modules not in sys.path:
            sys.path.append(modules)
        os.environ.setdefault("RESOLVE_SCRIPT_API", _API)
        os.environ.setdefault("RESOLVE_SCRIPT_LIB", _LIB)
        try:
            import DaVinciResolveScript as dvr
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise RuntimeError(
                "Could not import DaVinciResolveScript. Is DaVinci Resolve installed "
                "and the scripting API at the expected path?"
            ) from exc

    resolve = dvr.scriptapp("Resolve")
    if resolve is None:
        raise RuntimeError(
            "Resolve scripting app not reachable. Make sure DaVinci Resolve is "
            "running and (Preferences > System > General) external scripting is enabled."
        )
    return resolve


def get_context():
    """Return (resolve, project, media_pool, timeline). Raises if anything is missing."""
    resolve = get_resolve()
    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject()
    if project is None:
        raise RuntimeError("No project is open in Resolve.")
    media_pool = project.GetMediaPool()
    timeline = project.GetCurrentTimeline()
    if timeline is None:
        raise RuntimeError("No timeline is open. Open the timeline you want to cut.")
    return resolve, project, media_pool, timeline
