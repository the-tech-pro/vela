import os

import pytest

from antra.utils import runtime


@pytest.mark.parametrize(
    ("platform_name", "loader_vars"),
    [
        ("linux", ("LD_LIBRARY_PATH", "LD_PRELOAD")),
        (
            "darwin",
            (
                "DYLD_LIBRARY_PATH",
                "DYLD_FALLBACK_LIBRARY_PATH",
                "DYLD_FRAMEWORK_PATH",
                "DYLD_FALLBACK_FRAMEWORK_PATH",
                "DYLD_INSERT_LIBRARIES",
            ),
        ),
    ],
)
def test_clean_subprocess_env_removes_only_frozen_bundle_paths(
    monkeypatch, tmp_path, platform_name, loader_vars
):
    bundle_root = tmp_path / "_MEI-runtime"
    monkeypatch.setattr(runtime.sys, "platform", platform_name)
    monkeypatch.setattr(runtime.sys, "_MEIPASS", str(bundle_root), raising=False)

    expected = {}
    for index, var in enumerate(loader_vars):
        user_path = tmp_path / f"user-library-{index}"
        expected[var] = str(user_path)
        polluted_path = bundle_root if index == 0 else bundle_root / "lib"
        monkeypatch.setenv(
            var,
            os.pathsep.join((str(polluted_path), str(user_path))),
        )

    cleaned = runtime.get_clean_subprocess_env()

    for var, user_path in expected.items():
        assert cleaned[var] == user_path


@pytest.mark.parametrize(
    ("platform_name", "loader_var"),
    [
        ("linux", "LD_LIBRARY_PATH"),
        ("darwin", "DYLD_LIBRARY_PATH"),
    ],
)
def test_clean_subprocess_env_removes_empty_polluted_variable(
    monkeypatch, tmp_path, platform_name, loader_var
):
    bundle_root = tmp_path / "_MEI-runtime"
    monkeypatch.setattr(runtime.sys, "platform", platform_name)
    monkeypatch.setattr(runtime.sys, "_MEIPASS", str(bundle_root), raising=False)
    monkeypatch.setenv(loader_var, str(bundle_root / "lib"))

    cleaned = runtime.get_clean_subprocess_env()

    assert loader_var not in cleaned


@pytest.mark.parametrize(
    ("platform_name", "loader_var"),
    [
        ("linux", "LD_LIBRARY_PATH"),
        ("darwin", "DYLD_LIBRARY_PATH"),
        ("darwin", "DYLD_INSERT_LIBRARIES"),
    ],
)
def test_clean_subprocess_env_preserves_legitimate_frozen_environment(
    monkeypatch, tmp_path, platform_name, loader_var
):
    bundle_root = tmp_path / "_MEI-runtime"
    user_value = os.pathsep.join(
        (str(tmp_path / "user-library"), str(tmp_path / "other-library"))
    )
    monkeypatch.setattr(runtime.sys, "platform", platform_name)
    monkeypatch.setattr(runtime.sys, "_MEIPASS", str(bundle_root), raising=False)
    monkeypatch.setenv(loader_var, user_value)

    cleaned = runtime.get_clean_subprocess_env()

    assert cleaned[loader_var] == user_value


def test_clean_subprocess_env_preserves_non_frozen_environment(monkeypatch, tmp_path):
    loader_value = str(tmp_path / "_MEI-looking-but-user-owned")
    monkeypatch.setattr(runtime.sys, "platform", "darwin")
    monkeypatch.delattr(runtime.sys, "_MEIPASS", raising=False)
    monkeypatch.setenv("DYLD_LIBRARY_PATH", loader_value)

    cleaned = runtime.get_clean_subprocess_env()

    assert cleaned["DYLD_LIBRARY_PATH"] == loader_value


def test_approved_tool_prefers_configured_directory(monkeypatch, tmp_path):
    suffix = ".exe" if os.name == "nt" else ""
    configured_tool = tmp_path / "configured" / f"ffmpeg{suffix}"
    bundled_tool = tmp_path / "bundle" / "tools" / f"ffmpeg{suffix}"
    configured_tool.parent.mkdir()
    bundled_tool.parent.mkdir(parents=True)
    configured_tool.write_bytes(b"configured")
    bundled_tool.write_bytes(b"bundled")
    monkeypatch.setenv("VELA_TOOLS_DIR", str(configured_tool.parent))
    monkeypatch.setattr(
        runtime.sys, "_MEIPASS", str(bundled_tool.parent.parent), raising=False
    )

    assert runtime._approved_tool("ffmpeg") == str(configured_tool.resolve())


def test_approved_tool_finds_frozen_bundle(monkeypatch, tmp_path):
    suffix = ".exe" if os.name == "nt" else ""
    bundle_root = tmp_path / "_MEI-runtime"
    bundled_tool = bundle_root / "tools" / f"ffprobe{suffix}"
    bundled_tool.parent.mkdir(parents=True)
    bundled_tool.write_bytes(b"bundled")
    monkeypatch.delenv("VELA_TOOLS_DIR", raising=False)
    monkeypatch.setattr(runtime.sys, "_MEIPASS", str(bundle_root), raising=False)

    assert runtime._approved_tool("ffprobe") == str(bundled_tool.resolve())


def test_system_ffmpeg_discovery_returns_absolute_path(monkeypatch, tmp_path):
    ffmpeg = tmp_path / "relative-ffmpeg"
    ffmpeg.write_bytes(b"system")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(runtime, "_approved_tool", lambda _name: None)
    monkeypatch.setattr(runtime.shutil, "which", lambda _name: ffmpeg.name)

    assert runtime.get_ffmpeg_exe() == str(ffmpeg.resolve())


def test_meipass_ffmpeg_scan_is_deterministic_and_name_anchored(
    monkeypatch, tmp_path
):
    bundle_root = tmp_path / "_MEI-runtime"
    binaries = bundle_root / "imageio_ffmpeg" / "binaries"
    binaries.mkdir(parents=True)
    (binaries / "fake-ffmpeg").write_bytes(b"not-approved")
    preferred = binaries / "ffmpeg-a"
    preferred.write_bytes(b"preferred")
    (binaries / "ffmpeg-z").write_bytes(b"other")
    monkeypatch.setattr(runtime.sys, "_MEIPASS", str(bundle_root), raising=False)

    assert runtime._scan_meipass_ffmpeg() == str(preferred.resolve())
