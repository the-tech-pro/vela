from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Optional


_LINUX_LOADER_ENV_VARS = ("LD_LIBRARY_PATH", "LD_PRELOAD")
_DARWIN_LOADER_ENV_VARS = (
    "DYLD_LIBRARY_PATH",
    "DYLD_FALLBACK_LIBRARY_PATH",
    "DYLD_FRAMEWORK_PATH",
    "DYLD_FALLBACK_FRAMEWORK_PATH",
    "DYLD_INSERT_LIBRARIES",
)


def _approved_tool(name: str) -> Optional[str]:
    """Find a build-supplied tool without downloading or guessing binaries."""
    filename = name + (".exe" if os.name == "nt" else "")
    candidates = []
    configured = os.environ.get("VELA_TOOLS_DIR", "").strip()
    if configured:
        candidates.append(Path(configured) / filename)
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "tools" / filename)
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate.resolve())
    return None


def _scan_meipass_ffmpeg(name_contains: str = "ffmpeg", exclude: str = "ffprobe") -> Optional[str]:
    """Scan sys._MEIPASS/imageio_ffmpeg/binaries/ for the binary directly.

    imageio_ffmpeg's get_ffmpeg_exe() can fail in some PyInstaller environments
    even when the binary is present. This is the hard fallback.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if not meipass:
        return None
    binaries_dir = Path(meipass) / "imageio_ffmpeg" / "binaries"
    if not binaries_dir.is_dir():
        return None
    for f in sorted(binaries_dir.iterdir(), key=lambda path: path.name.lower()):
        n = f.name.lower()
        if n.startswith(name_contains) and exclude not in n and f.is_file():
            return str(f.resolve())
    return None


def get_ffmpeg_exe() -> Optional[str]:
    """Return the absolute path to the ffmpeg binary, or None if not found.

    Checks the approved tools bundle first, then system PATH, then the
    imageio_ffmpeg bundle. Finally, it scans _MEIPASS directly in case
    imageio_ffmpeg's own path resolution fails inside a frozen build.
    """
    approved = _approved_tool("ffmpeg")
    if approved:
        return approved
    system = shutil.which("ffmpeg")
    if system:
        return str(Path(system).resolve())
    try:
        from imageio_ffmpeg import get_ffmpeg_exe as _get
        exe = Path(_get())
        if exe.exists():
            return str(exe.resolve())
    except Exception:
        pass
    # Hard fallback: scan _MEIPASS directly (handles imageio_ffmpeg path
    # resolution failures that occur on some Windows machines in the bundle)
    return _scan_meipass_ffmpeg(name_contains="ffmpeg", exclude="ffprobe")


def get_ffprobe_exe() -> Optional[str]:
    """Return the absolute path to the ffprobe binary, or None if not found.

    imageio_ffmpeg ships ffprobe in the same directory as ffmpeg, so we
    derive the path from get_ffmpeg_exe() when system ffprobe is absent.
    """
    approved = _approved_tool("ffprobe")
    if approved:
        return approved
    system = shutil.which("ffprobe")
    if system:
        return str(Path(system).resolve())
    # imageio_ffmpeg bundles ffprobe alongside ffmpeg
    ffmpeg = get_ffmpeg_exe()
    if ffmpeg:
        ffprobe = Path(ffmpeg).parent / ("ffprobe.exe" if os.name == "nt" else "ffprobe")
        if ffprobe.exists():
            return str(ffprobe.resolve())
    return None


def _is_bundled_loader_path(value: str, meipass: str) -> bool:
    """Return whether a loader path points at or inside PyInstaller's bundle."""
    if not value:
        return False
    try:
        candidate = Path(value).resolve()
        bundle_root = Path(meipass).resolve()
    except (OSError, RuntimeError):
        return False
    return candidate == bundle_root or bundle_root in candidate.parents


def _strip_bundled_loader_paths(value: str, meipass: str) -> Optional[str]:
    """Remove only PyInstaller-derived entries from a loader path list."""
    remaining = [
        entry
        for entry in value.split(os.pathsep)
        if not _is_bundled_loader_path(entry, meipass)
    ]
    if not remaining:
        return None
    return os.pathsep.join(remaining)


def get_clean_subprocess_env() -> dict:
    """Return a child environment without frozen-runtime loader pollution.

    PyInstaller extracts bundled .so files into /tmp/_MEI*/ and adds that
    directory to LD_LIBRARY_PATH.  When ffmpeg/ffprobe is spawned as a child
    process it inherits this variable, causing system libraries (e.g.
    libcurl.so.4) to load the bundled libssl.so.3 instead of the system one —
    a version mismatch that crashes ffmpeg on Fedora 43 and similar distros.

    Frozen macOS helpers can likewise expose bundle-derived DYLD search paths
    to approved media tools. Only entries at or below sys._MEIPASS are removed;
    unrelated user loader paths and all non-frozen environments are preserved.
    """
    env = os.environ.copy()
    meipass = getattr(sys, "_MEIPASS", None)
    if not meipass:
        return env

    if sys.platform.startswith("linux"):
        loader_vars = _LINUX_LOADER_ENV_VARS
    elif sys.platform == "darwin":
        loader_vars = _DARWIN_LOADER_ENV_VARS
    else:
        return env

    for var in loader_vars:
        val = env.get(var, "")
        if not val:
            continue
        cleaned = _strip_bundled_loader_paths(val, os.fspath(meipass))
        if cleaned is not None:
            env[var] = cleaned
        else:
            env.pop(var, None)
    return env


def ensure_runtime_environment() -> None:
    exe = get_ffmpeg_exe()
    if not exe:
        return
    ffmpeg_dir = str(Path(exe).parent)
    current_path = os.environ.get("PATH", "")
    if ffmpeg_dir not in current_path.split(os.pathsep):
        os.environ["PATH"] = ffmpeg_dir + os.pathsep + current_path
    os.environ.setdefault("IMAGEIO_FFMPEG_EXE", exe)
