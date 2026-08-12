import json
import os
import shutil
import threading
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from antra.core.models import TrackMetadata
from antra.utils import organizer as organizer_module
from antra.utils.organizer import (
    IDENTITY_INDEX_FILE,
    IDENTITY_INDEX_SCHEMA_VERSION,
    STATE_FILE,
    LibraryOrganizer,
)


def make_track(token: str, *, album: str = "Album", playlist: str | None = None) -> TrackMetadata:
    return TrackMetadata(
        title=f"Song {token}",
        artists=["Artist"],
        album=album,
        isrc=token,
        track_number=1,
        playlist_name=playlist,
        playlist_position=1 if playlist else None,
        request_kind="playlist" if playlist else "album",
    )


def write_audio(path: Path, token: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token, encoding="utf-8")
    return path


def metadata_keys(path: Path) -> list[str]:
    token = path.read_text(encoding="utf-8").strip().lower()
    title = f"song {token}"
    return [
        f"isrc:{token}",
        f"title_artist:{title}:artist",
        f"title_artist_album:{title}:artist:album",
        f"title_artists:{title}:artist",
    ]


@contextmanager
def counted_metadata_probes():
    calls: list[Path] = []

    def probe(_organizer: LibraryOrganizer, path: Path) -> list[str]:
        calls.append(path)
        return metadata_keys(path)

    with patch.object(LibraryOrganizer, "_extract_identity_keys_from_file", probe):
        yield calls


def read_index(root: Path) -> dict:
    return json.loads((root / IDENTITY_INDEX_FILE).read_text(encoding="utf-8"))


def test_unchanged_library_probe_count_benchmark(tmp_path: Path):
    root = tmp_path / "library"
    for index in range(128):
        write_audio(root / f"{index:03d}.opus", f"bench{index:03d}")

    with counted_metadata_probes() as initial_probes:
        first = LibraryOrganizer(str(root))
    assert len(initial_probes) == 128
    assert first.flush()
    original_cache = (root / IDENTITY_INDEX_FILE).read_text(encoding="utf-8")

    with counted_metadata_probes() as repeated_probes:
        second = LibraryOrganizer(str(root))
    assert repeated_probes == []
    assert second.flush()
    assert (root / IDENTITY_INDEX_FILE).read_text(encoding="utf-8") == original_cache


def test_only_changed_file_is_reprobed(tmp_path: Path):
    root = tmp_path / "library"
    files = [
        write_audio(root / "a.opus", "a"),
        write_audio(root / "b.opus", "b"),
        write_audio(root / "c.opus", "c"),
    ]
    with counted_metadata_probes():
        LibraryOrganizer(str(root))

    files[1].write_text("b-changed", encoding="utf-8")
    with counted_metadata_probes() as probes:
        organizer = LibraryOrganizer(str(root))

    assert probes == [files[1]]
    assert organizer.is_already_downloaded(make_track("b-changed")) == str(files[1].resolve())
    assert organizer.is_already_downloaded(make_track("b")) is None
    assert organizer.flush()


def test_deletion_and_move_reconcile_without_metadata_probe(tmp_path: Path):
    root = tmp_path / "library"
    deleted = write_audio(root / "delete.opus", "deleted")
    source = write_audio(root / "move.opus", "moved")
    if source.stat().st_ino == 0:
        pytest.skip("filesystem does not expose stable file identities")

    with counted_metadata_probes():
        LibraryOrganizer(str(root))
    deleted.unlink()
    destination = root / "nested" / "moved.opus"
    destination.parent.mkdir()
    source.rename(destination)

    with counted_metadata_probes() as probes:
        organizer = LibraryOrganizer(str(root))

    assert probes == []
    assert organizer.is_already_downloaded(make_track("deleted")) is None
    assert organizer.is_already_downloaded(make_track("moved")) == str(destination.resolve())
    records = read_index(root)["records"]
    assert len(records) == 1
    assert next(iter(records.values()))["path"] == str(destination.resolve())


def test_hardlink_reuses_identity_while_copy_is_probed_once(tmp_path: Path):
    root = tmp_path / "library"
    source = write_audio(root / "source.opus", "shared")
    if source.stat().st_ino == 0:
        pytest.skip("filesystem does not expose stable file identities")
    with counted_metadata_probes():
        LibraryOrganizer(str(root))

    hardlink = root / "hardlink.opus"
    try:
        os.link(source, hardlink)
    except OSError as exc:
        pytest.skip(f"hard links unavailable: {exc}")
    copied = root / "copied.opus"
    shutil.copy2(source, copied)

    with counted_metadata_probes() as probes:
        organizer = LibraryOrganizer(str(root))

    assert probes == [copied]
    candidates = organizer._identity_candidates["isrc:shared"]
    assert {Path(path) for path in candidates} == {
        source.resolve(),
        hardlink.resolve(),
        copied.resolve(),
    }
    with counted_metadata_probes() as repeated_probes:
        LibraryOrganizer(str(root))
    assert repeated_probes == []


def test_corrupt_partial_schema_and_root_cache_rebuild(tmp_path: Path):
    root = tmp_path / "library"
    write_audio(root / "a.opus", "a")
    write_audio(root / "b.opus", "b")
    with counted_metadata_probes():
        LibraryOrganizer(str(root))
    cache_path = root / IDENTITY_INDEX_FILE

    cache_path.write_text('{"schema_version":', encoding="utf-8")
    with counted_metadata_probes() as corrupt_probes:
        LibraryOrganizer(str(root))
    assert len(corrupt_probes) == 2
    assert read_index(root)["schema_version"] == IDENTITY_INDEX_SCHEMA_VERSION

    partial = read_index(root)
    partial["complete"] = False
    cache_path.write_text(json.dumps(partial), encoding="utf-8")
    with counted_metadata_probes() as partial_probes:
        LibraryOrganizer(str(root))
    assert len(partial_probes) == 2
    assert read_index(root)["complete"] is True

    old_schema = read_index(root)
    old_schema["schema_version"] = IDENTITY_INDEX_SCHEMA_VERSION - 1
    cache_path.write_text(json.dumps(old_schema), encoding="utf-8")
    with counted_metadata_probes() as migration_probes:
        LibraryOrganizer(str(root))
    assert len(migration_probes) == 2

    moved_root = tmp_path / "moved-library"
    root.rename(moved_root)
    with counted_metadata_probes() as root_probes:
        LibraryOrganizer(str(moved_root))
    assert len(root_probes) == 2
    assert read_index(moved_root)["root"] == LibraryOrganizer._normalized_path(moved_root)


def test_completed_state_writes_are_batched_and_atomic(tmp_path: Path):
    root = tmp_path / "library"
    paths = [
        write_audio(root / f"{index}.opus", f"batch{index}")
        for index in range(10)
    ]
    with counted_metadata_probes():
        organizer = LibraryOrganizer(str(root))

    writes: list[str] = []
    original_atomic_replace = organizer_module._atomic_replace_json

    def record_write(path: Path, value: dict) -> bool:
        writes.append(path.name)
        return original_atomic_replace(path, value)

    errors: list[BaseException] = []

    def mark(index: int):
        try:
            organizer.mark_downloaded(make_track(f"batch{index}"), str(paths[index]))
        except BaseException as exc:  # surfaced after joining the worker
            errors.append(exc)

    with (
        patch.object(organizer_module, "_PERSIST_DELAY_SECONDS", 60.0),
        patch.object(organizer_module, "_atomic_replace_json", record_write),
    ):
        threads = [threading.Thread(target=mark, args=(index,)) for index in range(10)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        assert organizer.flush()

    assert errors == []
    assert all(not thread.is_alive() for thread in threads)
    assert writes.count(STATE_FILE) == 3
    state = json.loads((root / STATE_FILE).read_text(encoding="utf-8"))
    assert sum(key.startswith("TRACK:isrc:batch") for key in state) == 10
    assert list(root.glob("*.tmp")) == []


def test_concurrent_instances_merge_state_and_identity_updates(tmp_path: Path):
    root = tmp_path / "library"
    first = LibraryOrganizer(str(root))
    second = LibraryOrganizer(str(root))
    paths = [
        write_audio(root / f"{index}.opus", f"concurrent{index}")
        for index in range(6)
    ]
    barrier = threading.Barrier(2)
    results: list[bool] = []
    errors: list[BaseException] = []

    def persist(organizer: LibraryOrganizer, indexes: range):
        try:
            barrier.wait(timeout=5)
            for index in indexes:
                organizer.mark_downloaded(
                    make_track(f"concurrent{index}"),
                    str(paths[index]),
                )
            results.append(organizer.flush())
        except BaseException as exc:
            errors.append(exc)

    with patch.object(organizer_module, "_PERSIST_DELAY_SECONDS", 60.0):
        threads = [
            threading.Thread(target=persist, args=(first, range(0, 3))),
            threading.Thread(target=persist, args=(second, range(3, 6))),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

    assert errors == []
    assert results == [True, True]
    state = json.loads((root / STATE_FILE).read_text(encoding="utf-8"))
    assert sum(key.startswith("TRACK:isrc:concurrent") for key in state) == 6
    assert len(read_index(root)["records"]) == 6


def test_busy_lock_keeps_updates_for_a_later_flush(tmp_path: Path):
    root = tmp_path / "library"
    organizer = LibraryOrganizer(str(root))
    audio = write_audio(root / "locked.opus", "locked")
    state_lock = root / f"{STATE_FILE}.lock"

    with patch.object(organizer_module, "_PERSIST_DELAY_SECONDS", 60.0):
        organizer.mark_downloaded(make_track("locked"), str(audio))
        state_lock.write_text("other-process", encoding="ascii")
        assert organizer.flush() is False
        assert organizer._pending_state_updates
        state_lock.unlink()
        assert organizer.flush() is True

    state = json.loads((root / STATE_FILE).read_text(encoding="utf-8"))
    assert state["TRACK:isrc:locked"] == str(audio.resolve())


def test_changed_tagged_file_does_not_inherit_stale_completed_identity(tmp_path: Path):
    root = tmp_path / "library"
    audio = write_audio(root / "shared.opus", "original")
    old_track = TrackMetadata(
        title="Shared Song",
        artists=["Old Artist"],
        album="Album",
        isrc="OLD",
        track_number=1,
    )
    old_keys = [
        "isrc:old",
        "title_artist:shared song:old artist",
        "title_artist_album:shared song:old artist:album",
        "title_artists:shared song:old artist",
    ]

    with patch.object(
        LibraryOrganizer,
        "_extract_identity_keys_from_file",
        lambda _organizer, _path: old_keys,
    ):
        organizer = LibraryOrganizer(str(root))
    organizer.mark_downloaded(old_track, str(audio))
    assert organizer.flush()

    audio.write_text("replacement-with-a-different-size", encoding="utf-8")
    new_track = TrackMetadata(
        title="Shared Song",
        artists=["New Artist"],
        album="Album",
        isrc="NEW",
        track_number=1,
    )
    new_keys = [
        "isrc:new",
        "title_artist:shared song:new artist",
        "title_artist_album:shared song:new artist:album",
        "title_artists:shared song:new artist",
    ]
    with patch.object(
        LibraryOrganizer,
        "_extract_identity_keys_from_file",
        lambda _organizer, _path: new_keys,
    ):
        rebuilt = LibraryOrganizer(str(root))

    assert rebuilt.is_already_downloaded(old_track) is None
    assert rebuilt.is_already_downloaded(new_track) == str(audio.resolve())
    assert "isrc:old" not in next(iter(read_index(root)["records"].values()))["keys"]
    state = json.loads((root / STATE_FILE).read_text(encoding="utf-8"))
    assert state["TRACK:isrc:old"] == str(audio.resolve())


def test_identity_dedup_materialization_collision_and_completed_state(tmp_path: Path):
    root = tmp_path / "library"
    source = write_audio(root / "source.opus", "wanted")
    with counted_metadata_probes():
        organizer = LibraryOrganizer(str(root))
    track = make_track("wanted", playlist="Requested Mix")

    assert organizer.is_already_downloaded(track) == str(source.resolve())
    materialized = Path(organizer.ensure_request_copy(track, str(source)))
    assert materialized.exists()
    assert materialized != source
    assert organizer.has_exact_output(track)
    assert organizer.flush()

    state = json.loads((root / STATE_FILE).read_text(encoding="utf-8"))
    assert state["TRACK:isrc:wanted"] == str(materialized.resolve())
    with counted_metadata_probes() as reload_probes:
        reloaded = LibraryOrganizer(str(root))
    assert reload_probes == []
    assert reloaded.has_exact_output(track)

    collision_track = make_track("collision", album="Collision Album")
    collision_base = Path(reloaded.get_output_path(collision_track))
    collision_file = Path(f"{collision_base}.opus")
    write_audio(collision_file, "different")
    with counted_metadata_probes() as collision_probes:
        alternate = reloaded.get_output_path(collision_track)
    assert collision_probes == [collision_file]
    assert alternate.endswith(" (2)")
    assert reloaded.flush()

    with counted_metadata_probes() as cached_collision_probes:
        final = LibraryOrganizer(str(root))
        assert final.get_output_path(collision_track).endswith(" (2)")
    assert cached_collision_probes == []

    full_albums = LibraryOrganizer(str(root), full_albums=True)
    elsewhere = make_track("wanted", album="Another Album")
    assert full_albums.is_already_downloaded(elsewhere) is None
