# -*- mode: python ; coding: utf-8 -*-
"""Platform-aware PyInstaller build for Vela's Python backend.

Windows/Linux use a one-file executable. macOS uses a nested app-bundle helper
so executable code, frameworks, and resources occupy valid signed locations
and are never unpacked to a temporary directory at runtime. VELA_TOOLS_DIR
must contain ffmpeg, ffprobe and fpcalc.
"""

import os
import sys
from pathlib import Path
from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_submodules,
    collect_dynamic_libs,
    copy_metadata,
)

ROOT = Path.cwd().parent  # Antra/
ENTRY = ROOT / "antra" / "json_cli.py"
IS_DARWIN = sys.platform == "darwin"
if IS_DARWIN:
    os.environ.setdefault("MACOSX_DEPLOYMENT_TARGET", "12.0")
TARGET_ARCH = os.environ.get("VELA_TARGET_ARCH") or None
if TARGET_ARCH == "amd64":
    TARGET_ARCH = "x86_64"
if not IS_DARWIN:
    TARGET_ARCH = None
if not os.environ.get("VELA_TOOLS_DIR", "").strip():
    raise SystemExit(
        "VELA_TOOLS_DIR must contain approved native ffmpeg, ffprobe and fpcalc; "
        "run builds through build_desktop.py (see docs/desktop-builds.md)"
    )
TOOLS_DIR = Path(os.environ["VELA_TOOLS_DIR"]).resolve()
TOOL_SUFFIX = ".exe" if sys.platform == "win32" else ""
tool_binaries = [
    (str(TOOLS_DIR / f"{name}{TOOL_SUFFIX}"), "tools")
    for name in ("ffmpeg", "ffprobe", "fpcalc")
]

# ── Hidden imports ──────────────────────────────────────────────────────────
# PyInstaller misses dynamically-imported modules; list them explicitly.
hiddenimports = (
    collect_submodules("antra")
    + (collect_submodules("pytsk3") if sys.platform == "win32" else [])
    + collect_submodules("spotipy")
    + collect_submodules("mutagen")
    + collect_submodules("requests")
    + collect_submodules("urllib3")
    + collect_submodules("lyricsgenius")
    + collect_submodules("platformdirs")
    + collect_submodules("iopenpod", filter=lambda name: not name.startswith("iopenpod.gui"))
    + collect_submodules("numpy")
    + collect_submodules("Cryptodome")  # pycryptodomex — used for Python CENC fallback in Amazon adapter
    + [
        # dotenv
        "dotenv", "dotenv.main", "dotenv.compat", "dotenv.variables",
        # async runtime
        "asyncio", "asyncio.runners", "asyncio.tasks", "asyncio.events",
        # stdlib extras sometimes missed
        "email.mime.text", "email.mime.multipart", "email.mime.base",
        "xml.etree.ElementTree",
        "http.cookiejar",
        "zipfile", "tarfile",
        # spotipy internals
        "spotipy.oauth2", "spotipy.cache_handler",
        # imageio_ffmpeg — bundled ffmpeg used by Python sources
        "imageio_ffmpeg",
        # New fetchers added: SoundCloud, Amazon Music, SpotFetch
        "antra.core.soundcloud_fetcher",
        "antra.core.amazon_music_fetcher",
        "antra.core.spotfetch_fetcher",
        "antra.core.apple_fetcher",
        # Mirror server adapters
        "antra.sources.tidal_mirror",
        "antra.sources.qobuz_mirror",
        "antra.sources.deezer_mirror",
    ]
)

# ── Data files ───────────────────────────────────────────────────────────────
datas = []
for package in ("imageio_ffmpeg", "certifi", "lyricsgenius", "spotipy", "iopenpod"):
    try:
        datas += collect_data_files(package)
    except Exception:
        pass
if sys.platform == "win32":
    datas += copy_metadata("pytsk3")
datas.append((str(ROOT / "THIRD_PARTY_NOTICES.md"), "."))

# Explicitly exclude playwright's driver directory (node.exe + JS bundle = ~97 MB).
# We replaced the playwright session API with raw websockets CDP calls, so node.exe
# is never executed. The playwright Python package itself is still imported only for
# `playwright install chromium` (run as a subprocess), so we keep the Python code
# but strip the 97 MB data payload.
datas = [(src, dst) for src, dst in datas
         if "playwright" not in src.replace("\\", "/").lower()]
# imageio-ffmpeg marks its binaries directory as package data. Its README is
# not needed at runtime and macOS codesign can misclassify it as unsigned
# nested code when PyInstaller preserves that directory's executable mode.
datas = [
    (src, dst)
    for src, dst in datas
    if not (
        src.replace("\\", "/").lower().endswith("/imageio_ffmpeg/binaries/readme.md")
    )
]

# ── Analysis ─────────────────────────────────────────────────────────────────
# NOTE: collect_data_files("imageio_ffmpeg") already collects the ffmpeg binary
# into datas (as imageio_ffmpeg/binaries/ffmpeg-*.exe). Do NOT also add it to
# binaries= — that would cause PyInstaller to UPX-compress it, which conflicts
# with the datas copy and can cause the binary to silently fail on some machines.
a = Analysis(
    [str(ENTRY)],
    pathex=[str(ROOT)],
    binaries=tool_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # GUI toolkits — never needed in a CLI/subprocess binary
        "PySide6", "PyQt5", "PyQt6", "tkinter", "wx",
        # Test frameworks
        "pytest", "pytest_mock", "_pytest",
        # Jupyter / IPython
        "IPython", "jupyter", "notebook",
        # Large analysis/data packages not needed by the headless backend.
        # NumPy is required by iOpenPod artwork/database paths and is retained.
        "matplotlib", "scipy", "pandas",
        # playwright.async_api and playwright.sync_api are NOT imported at runtime
        # (we use raw websockets CDP). Only playwright._impl is needed for the
        # `playwright install chromium` subprocess call path, but even that is
        # optional. Excluding the whole package drops node.exe (~86 MB) from the build.
        # The `playwright install chromium` subprocess call works because it invokes
        # the system Python's playwright, not the bundled one.
        "playwright",
    ],
    noarchive=False,
    optimize=1,  # compile .pyc with basic optimisations (strips docstrings)
)
# The built-in imageio-ffmpeg hook can add this file again during Analysis,
# after the explicit data list above has been filtered.
a.datas = [
    item
    for item in a.datas
    if not item[0].replace("\\", "/").lower().endswith(
        "imageio_ffmpeg/binaries/readme.md"
    )
]

pyz = PYZ(a.pure)

exe_kwargs = dict(
    name="VelaBackend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=not IS_DARWIN,
    upx_exclude=["vcruntime140.dll", "python3*.dll"],
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=TARGET_ARCH,
    codesign_identity=os.environ.get("VELA_PYINSTALLER_CODESIGN_IDENTITY") or None,
    entitlements_file=os.environ.get("VELA_BACKEND_ENTITLEMENTS") or None,
)

if IS_DARWIN:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        **exe_kwargs,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        name="VelaBackend",
        strip=False,
        upx=False,
    )
    backend_bundle_id = (
        os.environ.get("VELA_BUNDLE_ID", "com.example.vela.development")
        + ".backend"
    )
    app = BUNDLE(
        coll,
        name="VelaBackend.app",
        version=os.environ.get("VELA_PRODUCT_VERSION", "0.0.0"),
        bundle_identifier=backend_bundle_id,
        info_plist={
            "CFBundleDisplayName": "VelaBackend",
            "LSBackgroundOnly": True,
            "LSMinimumSystemVersion": "12.0",
        },
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        runtime_tmpdir=None,
        **exe_kwargs,
    )
