from unittest.mock import MagicMock, patch

from antra import json_cli


def test_macos_browser_candidates_prefer_default_installed_family() -> None:
    installed = {
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    }
    with (
        patch.object(json_cli.sys, "platform", "darwin"),
        patch.object(
            json_cli,
            "_detect_macos_default_browser_family",
            return_value="brave",
        ),
        patch.object(json_cli.os.path, "exists", side_effect=installed.__contains__),
        patch.object(json_cli.os.path, "expanduser", return_value="/Users/fixture"),
    ):
        candidates = json_cli._browser_candidate_specs()

    assert candidates[0]["family"] == "brave"
    assert candidates[0]["executable_path"] in installed
    assert candidates[1]["family"] == "chrome"
    assert candidates[-1] == {
        "family": "chromium",
        "label": "Chromium (bundled)",
        "channel": "",
        "executable_path": "",
    }


def test_frozen_backend_never_downloads_playwright_browser(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(json_cli.sys, "frozen", True, raising=False)
    with (
        patch.object(json_cli.os.path, "isdir", return_value=False),
        patch.object(json_cli.subprocess, "run") as run,
    ):
        assert json_cli._get_playwright_chromium_exe() == ""

    run.assert_not_called()
    event = json_cli.json.loads(capsys.readouterr().out)
    assert event["level"] == "warning"
    assert "Chromium-family browser" in event["message"]


def test_debug_browser_launch_and_cleanup_use_isolated_profile() -> None:
    process = MagicMock()
    spec = {
        "family": "chrome",
        "label": "Chrome",
        "channel": "",
        "executable_path": "/Applications/Google Chrome",
    }
    with (
        patch.object(json_cli, "_browser_candidate_specs", return_value=[spec]),
        patch.object(json_cli.os.path, "exists", return_value=True),
        patch.object(json_cli, "_find_free_local_port", return_value=43123),
        patch.object(
            json_cli.tempfile,
            "mkdtemp",
            return_value="/tmp/vela-browser-fixture",
        ),
        patch.object(json_cli.subprocess, "Popen", return_value=process) as popen,
        patch.object(json_cli.shutil, "rmtree") as rmtree,
    ):
        state = json_cli._launch_debug_browser_process(
            "Apple Music",
            "https://fixture.invalid/login",
        )
        json_cli._cleanup_debug_browser(state)

    command = popen.call_args.args[0]
    assert command[0] == spec["executable_path"]
    assert "--remote-debugging-port=43123" in command
    assert "--user-data-dir=/tmp/vela-browser-fixture" in command
    assert command[-1] == "https://fixture.invalid/login"
    process.terminate.assert_called_once_with()
    process.wait.assert_called_once_with(timeout=5)
    rmtree.assert_called_once_with(
        "/tmp/vela-browser-fixture",
        ignore_errors=True,
    )
