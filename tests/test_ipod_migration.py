from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest

from antra.core.ipod_migration import (
    MigrationBundleError,
    build_migration_bundle,
    load_migration_bundle,
)


def _blob(root: Path, data: bytes) -> dict[str, object]:
    digest = hashlib.sha256(data).hexdigest()
    path = root / "blobs" / digest[:2] / digest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {"hash": digest, "size": len(data)}


def test_migration_bundle_rejects_manifest_traversal(tmp_path):
    backup_root = tmp_path / "backups"
    entry = _blob(backup_root, b"bad")

    with pytest.raises(MigrationBundleError) as failure:
        build_migration_bundle(
            {
                "../escape": entry,
                "iPod_Control/iTunes/iTunesDB": entry,
            },
            backup_root,
            tmp_path / "bundle",
            snapshot_fingerprint="a" * 64,
        )

    assert failure.value.code == "unsafe_backup_path"
    assert not (tmp_path / "escape").exists()


def test_migration_bundle_stages_only_verified_media_and_sidecar(tmp_path):
    backup_root = tmp_path / "backups"
    database = _blob(backup_root, b"database")
    media = _blob(backup_root, b"audio")
    sysinfo = _blob(backup_root, b"identity")
    artwork = _blob(backup_root, b"artwork")
    sqlite = _blob(backup_root, b"sqlite")
    entries = {
        "iPod_Control/iTunes/iTunesDB": database,
        "iPod_Control/Music/F00/song.mp3": media,
        "iPod_Control/Device/SysInfo": sysinfo,
        "iPod_Control/Artwork/ArtworkDB": artwork,
        "iPod_Control/iTunes/SQLiteDB/Library.itdb": sqlite,
    }
    library = {
        "mhlt": [{
            "track_id": 7,
            "db_track_id": 700,
            "Location": ":iPod_Control:Music:F00:song.mp3",
            "Title": "Source Title",
            "Artist": "Source Artist",
            "Album": "Source Album",
            "rating": 80,
            "play_count_1": 12,
            "length": 1000,
        }],
        "mhlp": [{
            "playlist_id": 9,
            "Title": "Exact MIX name",
            "items": [{"track_id": 7}],
        }],
    }
    bundle_path = tmp_path / "bundle"

    with patch(
        "iopenpod.itunesdb_parser.ipod_library.load_ipod_library",
        return_value=library,
    ):
        bundle = build_migration_bundle(
            entries,
            backup_root,
            bundle_path,
            snapshot_fingerprint="a" * 64,
        )

    files = {
        path.relative_to(bundle_path).as_posix()
        for path in bundle_path.rglob("*")
        if path.is_file()
    }
    assert files == {
        "bundle.json",
        bundle["tracks"][0]["staged_path"],
        bundle["playlists"][0]["staged_path"],
    }
    assert not (bundle_path / ".source-metadata").exists()
    assert all(
        excluded not in "/".join(files).casefold()
        for excluded in ("sysinfo", "artworkdb", "library.itdb", "sqlitedb")
    )
    assert bundle["tracks"][0]["metadata"]["rating"] == 80
    assert bundle["tracks"][0]["metadata"]["play_count_1"] == 12
    assert bundle["playlists"][0]["title"] == "Exact MIX name"

    media_path = bundle_path / bundle["tracks"][0]["staged_path"]
    media_path.write_bytes(b"changed")
    with pytest.raises(MigrationBundleError) as changed:
        load_migration_bundle(bundle_path, verify_media=True)
    assert changed.value.code == "migration_bundle_changed"


def test_migration_bundle_parses_real_iopenpod_database_metadata(tmp_path):
    from iopenpod.device import resolve_itdb_path
    from iopenpod.device.virtual import (
        available_virtual_ipod_models,
        create_virtual_ipod,
    )
    from iopenpod.sync.quick_writes import write_cached_itunesdb

    source = tmp_path / "source-ipod"
    model = next(
        row["model_number"]
        for row in available_virtual_ipod_models()
        if row["model_family"] == "iPod Classic"
    )
    create_virtual_ipod(source, model)
    media_path = source / "iPod_Control" / "Music" / "F00" / "song.mp3"
    media_path.parent.mkdir(parents=True, exist_ok=True)
    media_path.write_bytes(b"audio")
    write_result = write_cached_itunesdb(
        source,
        tracks_data=[{
            "Title": "Database Title",
            "Artist": "Database Artist",
            "Album": "Database Album",
            "Location": ":iPod_Control:Music:F00:song.mp3",
            "size": 5,
            "length": 1000,
            "track_id": 7,
            "db_track_id": 700,
            "rating": 100,
            "play_count_1": 42,
        }],
        playlists_data=[],
    )
    assert write_result.success is True
    database_path = Path(resolve_itdb_path(str(source)) or "")
    backup_root = tmp_path / "backups"
    entries = {
        f"iPod_Control/iTunes/{database_path.name}": _blob(
            backup_root,
            database_path.read_bytes(),
        ),
        "iPod_Control/Music/F00/song.mp3": _blob(
            backup_root,
            media_path.read_bytes(),
        ),
    }

    bundle = build_migration_bundle(
        entries,
        backup_root,
        tmp_path / "bundle",
        snapshot_fingerprint="b" * 64,
    )

    metadata = bundle["tracks"][0]["metadata"]
    assert metadata["Title"] == "Database Title"
    assert metadata["rating"] == 100
    assert metadata["play_count_1"] == 42
