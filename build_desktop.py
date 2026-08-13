#!/usr/bin/env python3
"""Build one architecture-native Vela desktop artifact.

Development builds allow placeholder macOS metadata. Release builds fail
closed unless their bundle identifier, icon, checksummed media tools, and
native architecture are explicitly validated. macOS builds are native-only:
run once on an arm64 host and once on an x86_64 host.
"""

import argparse
import ast
import hashlib
import importlib.util
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
WAILS_DIR = ROOT / "antra-wails"
SPEC_FILE = WAILS_DIR / "backend_runtime.spec"
BACKEND_DEST = WAILS_DIR / "runtime" / "backend"
MACOS_RESOURCES = ROOT / "packaging" / "macos"
PRODUCT_VERSION_FILE = ROOT / "antra" / "__init__.py"
TOOL_NAMES = ("ffmpeg", "ffprobe", "fpcalc")
BUILD_MODES = ("development", "release")
DEVELOPMENT_BUNDLE_ID = "com.example.vela.development"

_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_BUNDLE_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?$")
_PRODUCT_VERSION_RE = re.compile(r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$")


def normalized_arch(machine: str) -> str:
    value = machine.strip().lower()
    if value in {"arm64", "aarch64"}:
        return "arm64"
    if value in {"amd64", "x86_64"}:
        return "amd64"
    raise SystemExit(f"[ERROR] Unsupported architecture: {machine}")


def executable_name(name: str, platform_name: str | None = None) -> str:
    return name + (".exe" if (platform_name or sys.platform) == "win32" else "")


def resolve_build_mode(cli_value: str | None, environ=None) -> str:
    """Return the explicit build mode, defaulting safely to development."""
    environment = os.environ if environ is None else environ
    value = (cli_value or environment.get("VELA_BUILD_MODE") or "development").strip().lower()
    aliases = {"dev": "development", "production": "release", "prod": "release"}
    value = aliases.get(value, value)
    if value not in BUILD_MODES:
        raise SystemExit(
            f"[ERROR] Unsupported build mode {value!r}; expected development or release"
        )
    return value


def read_product_version(path: Path = PRODUCT_VERSION_FILE) -> str:
    """Read the canonical product version without importing the application."""
    try:
        module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as exc:
        raise SystemExit(f"[ERROR] Cannot read product version from {path}: {exc}") from exc
    for statement in module.body:
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
        if not any(isinstance(target, ast.Name) and target.id == "__version__" for target in targets):
            continue
        value = statement.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            version = value.value.strip()
            if _PRODUCT_VERSION_RE.fullmatch(version):
                return version
            raise SystemExit(
                f"[ERROR] Product version {version!r} in {path} must be three numeric components"
            )
    raise SystemExit(f"[ERROR] {path} does not define a literal __version__")


def validate_bundle_identifier(bundle_id: str, *, release: bool) -> str:
    """Validate Apple bundle syntax and reject placeholders for releases."""
    value = bundle_id.strip()
    labels = value.split(".")
    if len(labels) < 3 or any(not _BUNDLE_LABEL_RE.fullmatch(label) for label in labels):
        raise SystemExit(
            f"[ERROR] VELA_BUNDLE_ID must be a reverse-DNS identifier with at least "
            f"three valid labels, got {bundle_id!r}"
        )
    if release:
        domain_labels = {label.lower() for label in labels[:-1]}
        if (
            "example" in domain_labels
            or "invalid" in domain_labels
            or labels[0].lower() in {"dev", "local", "localhost"}
            or value.lower().endswith(".local")
        ):
            raise SystemExit(
                f"[ERROR] Release VELA_BUNDLE_ID cannot use a placeholder/local domain: {value}"
            )
    return value


def validate_icns(path: Path) -> Path:
    """Validate an explicitly supplied Apple icon container."""
    icon = path.expanduser().resolve()
    if icon.suffix.lower() != ".icns" or not icon.is_file():
        raise SystemExit(f"[ERROR] VELA_ICON_ICNS must be an existing .icns file: {icon}")
    try:
        with icon.open("rb") as stream:
            header = stream.read(8)
    except OSError as exc:
        raise SystemExit(f"[ERROR] Cannot read VELA_ICON_ICNS {icon}: {exc}") from exc
    if len(header) != 8 or header[:4] != b"icns":
        raise SystemExit(f"[ERROR] VELA_ICON_ICNS is not an Apple icon container: {icon}")
    declared_size = int.from_bytes(header[4:], byteorder="big")
    actual_size = icon.stat().st_size
    if declared_size != actual_size:
        raise SystemExit(
            f"[ERROR] Invalid .icns length for {icon}: header says {declared_size}, "
            f"file is {actual_size} bytes"
        )
    return icon


def run(cmd: list, cwd=None, desc=""):
    label = desc or " ".join(str(c) for c in cmd[:3])
    print(f"\n{'-'*60}")
    print(f"  {label}")
    print(f"{'-'*60}")
    result = subprocess.run(cmd, cwd=cwd or ROOT)
    if result.returncode != 0:
        print(f"\n[FAIL] '{label}' exited with code {result.returncode}")
        sys.exit(result.returncode)


def check_tools():
    missing = []
    # PyInstaller is invoked below as ``python -m PyInstaller``. Detect the
    # module in this interpreter instead of requiring its optional Scripts
    # launcher to be present on PATH (common with Microsoft Store Python).
    if importlib.util.find_spec("PyInstaller") is None:
        missing.append("pyinstaller")
    if not shutil.which("wails"):
        missing.append("wails")
    if missing:
        print(f"[ERROR] Missing required tools: {', '.join(missing)}")
        if "pyinstaller" in missing:
            print(f"  Install: {sys.executable} -m pip install pyinstaller")
        if "wails" in missing:
            print("  Install: https://wails.io/docs/gettingstarted/installation")
        sys.exit(1)


def check_upx():
    if sys.platform != "darwin" and not shutil.which("upx"):
        print("[WARN] UPX not found — binary will not be compressed (install UPX to reduce size)")
        print("       Download: https://github.com/upx/upx/releases")


def load_checksums(path: Path) -> dict[str, str]:
    manifest = path.expanduser().resolve()
    if not manifest.is_file():
        raise SystemExit(f"[ERROR] Checksum manifest does not exist: {manifest}")
    checksums: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        manifest.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 2 or not _SHA256_RE.fullmatch(parts[0]):
            raise SystemExit(
                f"[ERROR] Invalid SHA-256 line {line_number} in {manifest}: {raw_line}"
            )
        filename = parts[1].lstrip("*")
        if not filename or "/" in filename or "\\" in filename:
            raise SystemExit(
                f"[ERROR] Checksum filenames must be plain basenames in {manifest}: {filename!r}"
            )
        if filename in checksums:
            raise SystemExit(f"[ERROR] Duplicate checksum entry for {filename} in {manifest}")
        checksums[filename] = parts[0].lower()
    if not checksums:
        raise SystemExit(f"[ERROR] Checksum manifest is empty: {manifest}")
    return checksums


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def macho_architectures(path: Path) -> set[str]:
    """Return normalized architectures reported by macOS lipo."""
    result = subprocess.run(
        ["lipo", "-archs", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise SystemExit(f"[ERROR] Cannot inspect Mach-O architectures for {path}: {detail}")
    arches = {normalized_arch(value) for value in result.stdout.split()}
    if not arches:
        raise SystemExit(f"[ERROR] lipo reported no architecture for {path}")
    return arches


def validate_macho_architecture(
    path: Path,
    target_arch: str,
    *,
    require_thin: bool = False,
) -> None:
    """Require the target slice, and optionally reject additional slices."""
    actual = macho_architectures(path)
    expected = normalized_arch(target_arch)
    if expected not in actual or (require_thin and actual != {expected}):
        requirement = f"only {expected}" if require_thin else f"target slice {expected}"
        raise SystemExit(
            f"[ERROR] Architecture mismatch for {path}: expected {requirement}, "
            f"found {', '.join(sorted(actual))}"
        )


def is_macho_file(path: Path) -> bool:
    result = subprocess.run(
        ["file", "-b", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise SystemExit(f"[ERROR] Cannot inspect bundle file {path}: {detail}")
    return "Mach-O" in result.stdout


def validate_macos_bundle_architectures(app_bundle: Path, target_arch: str) -> list[Path]:
    """Validate every real Mach-O file in the assembled application bundle."""
    required = (
        app_bundle / "Contents" / "MacOS" / "Vela",
        app_bundle
        / "Contents"
        / "Helpers"
        / "VelaBackend.app"
        / "Contents"
        / "MacOS"
        / "VelaBackend",
    )
    for path in required:
        if not path.is_file():
            raise SystemExit(f"[ERROR] Required macOS executable is missing: {path}")
    for path in required:
        validate_macho_architecture(path, target_arch, require_thin=True)

    macho_files: list[Path] = []
    seen_files: set[tuple[int, int]] = set()
    for path in sorted(app_bundle.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink() or not path.is_file():
            continue
        stat = path.stat()
        identity = (stat.st_dev, stat.st_ino)
        if identity in seen_files:
            continue
        seen_files.add(identity)
        if is_macho_file(path):
            if path not in required:
                validate_macho_architecture(path, target_arch)
            macho_files.append(path)
    if not macho_files:
        raise SystemExit(f"[ERROR] No Mach-O code found in assembled bundle: {app_bundle}")
    print(f"  [OK] Validated {len(macho_files)} Mach-O files for {target_arch}")
    return macho_files


def validate_tools(
    *,
    build_mode: str,
    target_arch: str,
    platform_name: str | None = None,
    environ=None,
) -> Path:
    environment = os.environ if environ is None else environ
    current_platform = platform_name or sys.platform
    raw_dir = environment.get("VELA_TOOLS_DIR", "").strip()
    if not raw_dir:
        raise SystemExit(
            "[ERROR] VELA_TOOLS_DIR is required. Point it at architecture-matched, "
            "owner-approved ffmpeg, ffprobe and fpcalc binaries. Vela does not "
            "download tools automatically; see docs/desktop-builds.md."
        )
    tools_dir = Path(raw_dir).expanduser().resolve()
    if not tools_dir.is_dir():
        raise SystemExit(f"[ERROR] VELA_TOOLS_DIR does not exist: {tools_dir}")

    checksums_path = environment.get("VELA_TOOLS_CHECKSUMS", "").strip()
    if build_mode == "release" and current_platform == "darwin" and not checksums_path:
        raise SystemExit("[ERROR] Release macOS builds require VELA_TOOLS_CHECKSUMS")
    checksums = load_checksums(Path(checksums_path)) if checksums_path else {}
    for tool in TOOL_NAMES:
        path = tools_dir / executable_name(tool, current_platform)
        if not path.is_file():
            raise SystemExit(f"[ERROR] Missing required {tool}: {path}")
        expected = checksums.get(path.name)
        if checksums_path and not expected:
            raise SystemExit(f"[ERROR] No checksum entry for {path.name} in {checksums_path}")
        if expected:
            actual = sha256_file(path)
            if actual != expected:
                raise SystemExit(f"[ERROR] SHA-256 mismatch for {path}: expected {expected}, got {actual}")
        if current_platform == "darwin":
            validate_macho_architecture(path, target_arch)
    print(f"  [OK] Validated media tools in {tools_dir}")
    return tools_dir


def resolve_macos_metadata(build_mode: str, environ=None) -> tuple[str, Path | None]:
    environment = os.environ if environ is None else environ
    release = build_mode == "release"
    raw_bundle_id = environment.get("VELA_BUNDLE_ID", "").strip()
    if release and not raw_bundle_id:
        raise SystemExit("[ERROR] Release macOS builds require VELA_BUNDLE_ID")
    bundle_id = validate_bundle_identifier(
        raw_bundle_id or DEVELOPMENT_BUNDLE_ID,
        release=release,
    )

    raw_icon = environment.get("VELA_ICON_ICNS", "").strip()
    if release and not raw_icon:
        raise SystemExit(
            "[ERROR] Release macOS builds require an owner-approved VELA_ICON_ICNS"
        )
    icon_path = validate_icns(Path(raw_icon)) if raw_icon else None
    return bundle_id, icon_path


def install_macos_metadata(
    app_bundle: Path,
    *,
    build_mode: str,
    version: str,
    bundle_id: str,
    icon_path: Path | None,
) -> None:
    plist_template = MACOS_RESOURCES / "Info.plist"
    plist_dest = app_bundle / "Contents" / "Info.plist"
    plist = plistlib.loads(plist_template.read_bytes())
    plist["CFBundleIdentifier"] = bundle_id
    plist["CFBundleShortVersionString"] = version
    plist["CFBundleVersion"] = version
    plist["VelaBuildMode"] = build_mode
    if icon_path is not None:
        resources = app_bundle / "Contents" / "Resources"
        resources.mkdir(parents=True, exist_ok=True)
        shutil.copy2(icon_path, resources / "Vela.icns")
        plist["CFBundleIconFile"] = "Vela.icns"
    else:
        plist.pop("CFBundleIconFile", None)
    plist_dest.write_bytes(plistlib.dumps(plist, sort_keys=False))


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-arch", choices=("arm64", "amd64"))
    parser.add_argument(
        "--build-mode",
        choices=BUILD_MODES,
        help="development (default) or fail-closed release validation",
    )
    return parser.parse_args(argv)


def main():
    args = parse_args()
    build_mode = resolve_build_mode(args.build_mode)
    version = read_product_version()
    host_arch = normalized_arch(platform.machine())
    target_arch = normalized_arch(
        args.target_arch or os.environ.get("VELA_TARGET_ARCH") or host_arch
    )
    if target_arch != host_arch:
        raise SystemExit(
            f"[ERROR] Backend builds must be native: requested {target_arch}, host is {host_arch}. "
            "Use a matching host/runner for each architecture."
        )
    if sys.platform not in {"win32", "darwin"} and not sys.platform.startswith("linux"):
        raise SystemExit(f"[ERROR] Unsupported platform: {sys.platform}")

    print("\n" + "=" * 60)
    print(f"  VELA Desktop Build {version} ({build_mode}, {sys.platform}/{target_arch})")
    print("=" * 60)

    macos_metadata = resolve_macos_metadata(build_mode) if sys.platform == "darwin" else None
    check_tools()
    check_upx()
    tools_dir = validate_tools(
        build_mode=build_mode,
        target_arch=target_arch,
    )
    os.environ["VELA_BUILD_MODE"] = build_mode
    os.environ["VELA_TARGET_ARCH"] = target_arch
    os.environ["VELA_PRODUCT_VERSION"] = version
    os.environ["VELA_TOOLS_DIR"] = str(tools_dir)
    os.environ.setdefault("MACOSX_DEPLOYMENT_TARGET", "12.0")

    print("\n[1/3] Building Python backend (PyInstaller)...")
    shutil.rmtree(BACKEND_DEST, ignore_errors=True)
    BACKEND_DEST.mkdir(parents=True, exist_ok=True)

    # PyInstaller work dir (cache + temp) — keep it local so it doesn't pollute source
    work_dir = WAILS_DIR / "_pyinstaller_work"
    work_dir.mkdir(exist_ok=True)

    run(
        [
            sys.executable, "-m", "PyInstaller",
            str(SPEC_FILE),
            "--distpath", str(BACKEND_DEST),
            "--workpath", str(work_dir),
            "--noconfirm",
        ],
        cwd=WAILS_DIR,
        desc="PyInstaller - bundling Python backend",
    )

    if sys.platform == "darwin":
        backend_output = BACKEND_DEST / "VelaBackend.app"
        backend_exe = backend_output / "Contents" / "MacOS" / "VelaBackend"
    else:
        backend_output = BACKEND_DEST / executable_name("VelaBackend")
        backend_exe = backend_output
    if not backend_exe.is_file():
        print(f"[FAIL] Expected backend at {backend_exe} — not found after PyInstaller")
        sys.exit(1)

    size_mb = backend_exe.stat().st_size / (1024 * 1024)
    print(f"\n  [OK] {backend_exe.name} ({size_mb:.1f} MB launcher)")

    # Clean up PyInstaller work dir (not needed after build)
    shutil.rmtree(work_dir, ignore_errors=True)

    print("\n[2/3] Building Wails desktop app...")
    wails_cmd = ["wails", "build"]
    if sys.platform == "darwin":
        wails_cmd += ["-clean", "-platform", f"darwin/{target_arch}"]
    elif sys.platform.startswith("linux"):
        wails_cmd += ["-clean", "-platform", f"linux/{target_arch}"]
    run(
        wails_cmd,
        cwd=WAILS_DIR,
        desc="wails build — packaging Vela",
    )

    if sys.platform == "darwin":
        final_artifact = WAILS_DIR / "build" / "bin" / "Vela.app"
        if not final_artifact.is_dir():
            raise SystemExit(f"[FAIL] Expected app bundle at {final_artifact}")
        helper_dest = final_artifact / "Contents" / "Helpers" / "VelaBackend.app"
        shutil.rmtree(helper_dest, ignore_errors=True)
        helper_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(backend_output, helper_dest, symlinks=True)
        bundle_id, icon_path = macos_metadata
        install_macos_metadata(
            final_artifact,
            build_mode=build_mode,
            version=version,
            bundle_id=bundle_id,
            icon_path=icon_path,
        )
        validate_macos_bundle_architectures(final_artifact, target_arch)
    else:
        suffix = ".exe" if sys.platform == "win32" else ""
        final_artifact = WAILS_DIR / "build" / "bin" / f"Vela{suffix}"

    if not final_artifact.exists():
        print(f"[FAIL] Expected output at {final_artifact} — not found")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("  Build Complete")
    print("=" * 60)
    print(f"  Output : {final_artifact}")
    if sys.platform == "darwin":
        print("  Next   : packaging/macos/sign-and-package.sh (sign inside-out, then notarize)")
    else:
        print(f"  Size   : {final_artifact.stat().st_size / (1024 * 1024):.1f} MB")
    print("=" * 60)
    print()


if __name__ == "__main__":
    main()
