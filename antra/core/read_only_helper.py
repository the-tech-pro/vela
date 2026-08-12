"""Long-lived, read-only backend dispatcher for the Vela desktop.

The protocol is newline-delimited JSON. Stdout is reserved exclusively for
responses; diagnostics go to stderr without request parameters or secrets.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable, TextIO


PROTOCOL_VERSION = 1
MAX_REQUEST_BYTES = 1024 * 1024


class HelperProtocolError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _desktop_config(config_path: str | None):
    from antra.core.config import load_config

    cfg = load_config()
    if not config_path:
        return cfg
    path = Path(config_path)
    if not path.is_file():
        return cfg

    with path.open("r", encoding="utf-8") as config_file:
        settings = json.load(config_file)
    if not isinstance(settings, dict):
        raise RuntimeError("Desktop configuration must be a JSON object.")

    config_fields = {field.name for field in dataclasses.fields(cfg)}
    for key, value in settings.items():
        if key not in config_fields or key == "tracked_playlists":
            continue
        if key == "sources_enabled" and isinstance(value, list):
            value = ",".join(str(item).strip() for item in value if str(item).strip())
        setattr(cfg, key, value)

    configured_root = str(settings.get("download_path") or "").strip()
    if configured_root:
        configured_root = os.path.abspath(os.path.expanduser(configured_root))
        if not settings.get("download_path_is_library_root"):
            legacy_root = os.path.join(configured_root, "Apple Music")
            configured_root = (
                legacy_root
                if os.path.isdir(legacy_root)
                else os.path.join(configured_root, "Vela")
            )
        cfg.output_dir = configured_root

    download_sources = settings.get("download_sources")
    if isinstance(download_sources, list):
        cleaned = [str(source).strip() for source in download_sources if str(source).strip()]
        cfg.source_preference = ",".join(cleaned) if cleaned else "auto"
    elif "download_source" in settings:
        cfg.source_preference = str(settings.get("download_source") or "auto")

    tracked = settings.get("tracked_playlists")
    cfg.tracked_playlists = list(tracked) if isinstance(tracked, list) else []
    return cfg


def _apple_client(
    cfg,
    config_path: str | None,
    require_credentials: bool = True,
):
    from antra.core.apple_library import AppleLibraryClient

    authorization = str(cfg.apple_authorization_token or "").strip()
    music_user_token = str(cfg.apple_music_user_token or "").strip()
    storefront = str(cfg.apple_storefront or "gb").strip() or "gb"
    if require_credentials and (not authorization or not music_user_token):
        raise RuntimeError(
            "No Apple Music web session configured. "
            "Connect Apple Music in Settings to enable library access."
        )
    cache_path = None
    if config_path:
        cache_path = str(
            Path(config_path).resolve().parent / "apple_library_cache.sqlite3"
        )
    return AppleLibraryClient(
        authorization_token=authorization,
        music_user_token=music_user_token,
        storefront=storefront,
        cache_path=cache_path,
        cache_only=not require_credentials,
    )


def dispatch_read_only(
    command: str,
    params: dict[str, Any],
    config_path: str | None,
) -> Any:
    if command == "ffmpeg_paths":
        from antra.utils.runtime import get_ffmpeg_exe, get_ffprobe_exe

        return {
            "ffmpeg": get_ffmpeg_exe() or "",
            "ffprobe": get_ffprobe_exe() or "",
        }

    if command == "artist_discography":
        artist_url = _required_string(params, "artist_url")
        if "music.apple.com" in artist_url and "/artist/" in artist_url:
            from antra.core.apple_fetcher import AppleFetcher

            return AppleFetcher().fetch_artist_discography_info(artist_url)
        cfg = _desktop_config(config_path)
        if "music.amazon.com" in artist_url and "/artists/" in artist_url:
            from antra.core.amazon_music_fetcher import AmazonMusicFetcher
            from antra.core.endpoint_manifest import load_endpoint_manifest

            manifest = load_endpoint_manifest()
            return AmazonMusicFetcher(
                mirrors=cfg.amazon_mirrors or manifest.amazon
            ).fetch_artist_discography_info(artist_url)

        from antra.core.spotify import SpotifyClient

        spotify = SpotifyClient(
            cfg.spotify_client_id,
            cfg.spotify_client_secret,
            cfg.spotify_market,
            redirect_uri=cfg.spotify_redirect_uri,
            auth_storage_path=cfg.spotify_auth_path,
        )
        return spotify.fetch_artist_discography_info(artist_url)

    if command == "artist_search":
        from antra.core.service import AntraService

        query = _required_string(params, "query")
        source = str(params.get("source") or "spotify").strip().lower()
        if source not in {"spotify", "apple"}:
            raise HelperProtocolError("invalid_params", "Unsupported artist search source.")
        return AntraService(config=_desktop_config(config_path)).search_artists(
            query, source=source
        )

    if command == "discovery":
        from antra.core.discovery import AppleDiscovery

        region = str(params.get("region") or "us").strip() or "us"
        genre_id = str(params.get("genre_id") or "").strip() or None
        genre_name = str(params.get("genre_name") or "").strip() or None
        data = AppleDiscovery().get_discovery_data(
            storefront=region,
            genre_id=genre_id,
            genre_name=genre_name,
        )
        return {"type": "discovery", "data": data}

    if command == "discovery_genres":
        from antra.core.discovery import AppleDiscovery

        region = str(params.get("region") or "us").strip() or "us"
        genres = AppleDiscovery().get_genres(storefront=region)
        return {"type": "discovery_genres", "data": genres}

    if command == "apple_library":
        cfg = _desktop_config(config_path)
        with _apple_client(cfg, config_path) as client:
            return client.get_library(force_refresh=False)

    if command == "apple_library_detail":
        library_url = _required_string(params, "library_url")
        cfg = _desktop_config(config_path)
        with _apple_client(cfg, config_path) as client:
            return client.get_playlist_detail(library_url)

    if command == "apple_library_artist":
        artist_name = _required_string(params, "artist_name")
        cfg = _desktop_config(config_path)
        with _apple_client(
            cfg,
            config_path,
            require_credentials=False,
        ) as client:
            return client.get_artist_detail(artist_name)

    if command == "ipod_scan":
        from antra.core.ipod_service import IPodService

        app_data = (
            str(Path(config_path).resolve().parent)
            if config_path
            else os.getcwd()
        )
        return IPodService(app_data).scan()

    raise HelperProtocolError("unknown_command", "Unsupported read-only command.")


def _required_string(params: dict[str, Any], key: str) -> str:
    value = str(params.get(key) or "").strip()
    if not value:
        raise HelperProtocolError("invalid_params", f"Missing {key}.")
    return value


_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(authorization|password|secret|cookie|sp_dc|arl|"
    r"(?:music[_ -]?user|access|refresh)[_ -]?token)\b"
    r"(\s*[:=]\s*)([^\s,;]+)"
)
_BEARER_TOKEN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_QUERY_SECRET = re.compile(
    r"(?i)([?&](?:token|key|secret|authorization|signature)=)[^&#\s]+"
)


def safe_error_message(error: BaseException) -> str:
    message = str(error).strip() or error.__class__.__name__
    message = _SECRET_ASSIGNMENT.sub(r"\1\2<redacted>", message)
    message = _BEARER_TOKEN.sub("Bearer <redacted>", message)
    message = _QUERY_SECRET.sub(r"\1<redacted>", message)
    return message[:2000]


def serve_read_only_helper(
    config_path: str | None,
    input_stream: TextIO = sys.stdin,
    output_stream: TextIO = sys.stdout,
    error_stream: TextIO = sys.stderr,
    dispatcher: Callable[[str, dict[str, Any], str | None], Any] = dispatch_read_only,
) -> int:
    while True:
        raw_line = input_stream.readline(MAX_REQUEST_BYTES + 1)
        if raw_line == "":
            return 0
        if len(raw_line.encode("utf-8")) > MAX_REQUEST_BYTES:
            if not raw_line.endswith("\n"):
                _discard_line_remainder(input_stream)
            _write_response(
                output_stream,
                {
                    "id": None,
                    "ok": False,
                    "error": {
                        "code": "request_too_large",
                        "message": "Read-only helper request exceeded the size limit.",
                    },
                },
            )
            continue

        request_id: Any = None
        command = ""
        try:
            request = json.loads(raw_line)
            if not isinstance(request, dict):
                raise HelperProtocolError("invalid_request", "Request must be a JSON object.")
            request_id = request.get("id")
            if (
                isinstance(request_id, bool)
                or not isinstance(request_id, (str, int))
                or request_id == ""
            ):
                raise HelperProtocolError("invalid_request", "Request id is required.")
            if request.get("protocol_version") != PROTOCOL_VERSION:
                raise HelperProtocolError(
                    "unsupported_protocol",
                    "Unsupported read-only helper protocol version.",
                )
            command = str(request.get("command") or "").strip()
            if not command:
                raise HelperProtocolError("invalid_request", "Command is required.")
            params = request.get("params") or {}
            if not isinstance(params, dict):
                raise HelperProtocolError("invalid_params", "Params must be a JSON object.")
            result = dispatcher(command, params, config_path)
            response = {"id": request_id, "ok": True, "result": result}
        except HelperProtocolError as error:
            response = {
                "id": request_id,
                "ok": False,
                "error": {"code": error.code, "message": safe_error_message(error)},
            }
        except json.JSONDecodeError:
            response = {
                "id": request_id,
                "ok": False,
                "error": {
                    "code": "invalid_json",
                    "message": "Request was not valid JSON.",
                },
            }
        except Exception as error:
            # Never echo params or exception text to stderr: either may contain
            # credentials. The sanitized detail is returned only to the caller.
            print(
                f"read-only helper command failed: {command or '<invalid>'}: "
                f"{error.__class__.__name__}",
                file=error_stream,
                flush=True,
            )
            response = {
                "id": request_id,
                "ok": False,
                "error": {
                    "code": "command_failed",
                    "message": safe_error_message(error),
                },
            }
        _write_response(output_stream, response)


def _discard_line_remainder(input_stream: TextIO) -> None:
    while True:
        remainder = input_stream.readline(MAX_REQUEST_BYTES + 1)
        if remainder == "" or remainder.endswith("\n"):
            return


def _write_response(output_stream: TextIO, response: dict[str, Any]) -> None:
    # Keep the wire representation ASCII-only. Frozen Windows helpers can expose
    # a legacy console encoding even when the launcher requests UTF-8; emitting
    # raw Unicode would then terminate the helper before Go receives a response.
    # JSON decoders restore escaped Unicode transparently.
    output_stream.write(
        json.dumps(response, ensure_ascii=True, separators=(",", ":"), default=str)
        + "\n"
    )
    output_stream.flush()
