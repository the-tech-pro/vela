from __future__ import annotations

from types import SimpleNamespace

import pytest

from antra.core.ipod_service import IOpenPodAdapter, IPodService, IPodServiceError
from antra.core.ipod_windows_raw import (
    WindowsRawIPod,
    _probe_hfs_ipod,
    scan_windows_raw_ipods,
)


class FakeImage:
    def __init__(self, path: str):
        self.path = path

    def get_size(self) -> int:
        return 80_000_000_000


class FakeVolume:
    def __init__(self, image: FakeImage):
        self.image = image
        self.info = SimpleNamespace(block_size=4096)

    def __iter__(self):
        return iter(
            [
                SimpleNamespace(desc=b"Apple_partition_map", start=1, len=62),
                SimpleNamespace(desc=b"Apple_HFS", start=64, len=19_531_186),
            ]
        )


class FakeFilesystem:
    def __init__(self, image: FakeImage, *, offset: int, root_names: tuple[str, ...]):
        self.image = image
        self.offset = offset
        self.root_names = root_names
        self.info = SimpleNamespace(
            fs_id=list(b"0123456789abcdef") + [0] * 16,
            fs_id_used=16,
        )

    def open_dir(self, *, path: str):
        assert path == "/"
        return [
            SimpleNamespace(
                info=SimpleNamespace(
                    name=SimpleNamespace(name=name.encode("utf-8"))
                )
            )
            for name in self.root_names
        ]


class FakePytsk:
    def __init__(self, root_names: tuple[str, ...] = ("iPod_Control",)):
        self.root_names = root_names

    Img_Info = FakeImage
    Volume_Info = FakeVolume

    def FS_Info(self, image: FakeImage, *, offset: int):
        return FakeFilesystem(
            image,
            offset=offset,
            root_names=self.root_names,
        )


def raw_ipod(path: str = "G:\\") -> WindowsRawIPod:
    return WindowsRawIPod(
        path=path,
        raw_device_path=r"\\.\G:",
        display_name="Mac-formatted iPod (G:)",
        ipod_name="Mac-formatted iPod (G:)",
        disk_size_gb=80.0,
        capacity="80 GB",
        hfs_partition_offset_bytes=64 * 4096,
        raw_volume_fingerprint="a" * 32,
    )


def test_probe_recognizes_hfs_ipod_without_reading_file_contents() -> None:
    device = _probe_hfs_ipod("G:\\", pytsk_module=FakePytsk())

    assert device is not None
    assert device.path == "G:\\"
    assert device.raw_device_path == r"\\.\G:"
    assert device.filesystem_type == "HFS+"
    assert device.filesystem_accessible is False
    assert device.raw_read_only is True
    assert device.hfs_partition_offset_bytes == 64 * 4096
    assert len(device.raw_volume_fingerprint) == 32


def test_probe_rejects_non_ipod_hfs_volume() -> None:
    assert (
        _probe_hfs_ipod(
            "G:\\",
            pytsk_module=FakePytsk(root_names=("Users", "Applications")),
        )
        is None
    )


def test_scan_skips_mounted_duplicate_and_fails_closed() -> None:
    calls: list[str] = []

    def probe(path: str) -> WindowsRawIPod | None:
        calls.append(path)
        if path == "H:\\":
            raise OSError("unreadable")
        return raw_ipod(path)

    devices = scan_windows_raw_ipods(
        ["G:\\"],
        drive_letters=["G", "H", "I", "invalid"],
        probe=probe,
    )

    assert calls == ["H:\\", "I:\\"]
    assert [device.path for device in devices] == ["I:\\"]


def test_service_exposes_raw_device_but_adapter_blocks_operations(tmp_path) -> None:
    device = raw_ipod()
    service_adapter = SimpleNamespace(scan_read_only=lambda: [device])
    summary = IPodService(tmp_path, adapter=service_adapter).scan()["devices"][0]

    assert summary["name"] == "Mac-formatted iPod (G:)"
    assert summary["filesystem_accessible"] is False
    assert summary["access_state"] == "mac_formatted_read_only"
    assert summary["browse_only"] is True
    assert summary["needs_preparation"] is True
    assert "Mac-formatted HFS+" in summary["write_block_reason"]

    adapter = object.__new__(IOpenPodAdapter)
    adapter.scan_read_only = lambda: [device]  # type: ignore[method-assign]
    with pytest.raises(IPodServiceError) as blocked:
        adapter.identify_read_only("G:\\")
    assert blocked.value.code == "filesystem_unavailable"


def test_raw_device_id_is_stable_across_drive_letter_changes(tmp_path) -> None:
    first_adapter = SimpleNamespace(scan_read_only=lambda: [raw_ipod("G:\\")])
    second_adapter = SimpleNamespace(scan_read_only=lambda: [raw_ipod("I:\\")])

    first_id = IPodService(tmp_path / "first", adapter=first_adapter).scan()[
        "devices"
    ][0]["device_id"]
    second_id = IPodService(tmp_path / "second", adapter=second_adapter).scan()[
        "devices"
    ][0]["device_id"]

    assert first_id == second_id


def test_mount_dependent_routes_reject_raw_hfs_device_before_access(tmp_path) -> None:
    device = raw_ipod()
    adapter = object.__new__(IOpenPodAdapter)
    adapter.scan_read_only = lambda: [device]  # type: ignore[method-assign]
    service = IPodService(tmp_path, adapter=adapter)

    calls = [
        lambda: service.browse("G:\\", "tracks"),
        lambda: service.manual_backup("G:\\"),
        lambda: service.create_plan("G:\\", []),
        lambda: service.eject("G:\\"),
        lambda: service.create_staging_contract("G:\\", [], str(tmp_path)),
        lambda: service.capacity_unlock_eligibility("G:\\"),
    ]
    for call in calls:
        with pytest.raises(IPodServiceError) as blocked:
            call()
        assert blocked.value.code == "filesystem_unavailable"
