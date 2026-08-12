"""Pinned artifact metadata and fail-closed download/validation helpers.

Nothing is downloaded at import time.  Network transfer requires the caller to
pass ``explicit_user_action=True`` and always writes to a temporary file in the
destination directory before an atomic replace.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import stat
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from contextlib import AbstractContextManager
from pathlib import Path
from types import MappingProxyType
from typing import Any, BinaryIO, Callable, Mapping, Protocol

APPLE_IPSW_URL = (
    "https://secure-appldnld.apple.com/iPod/SBML/osx/bundles/"
    "061-6797.20090909.3uTfE/iPod_35.2.0.2.ipsw"
)
APPLE_IPSW_SIZE = 61_033_067
APPLE_IPSW_SHA1 = "5a7ab72fd7e299118bb0f25adfea1ad4808e1f0a"
APPLE_IPSW_SHA256 = (
    "a12f25067a821850979efe8222de6e2bb98eba985ba21f61abe386355c6655b4"
)

ROCKBOX_UTILITY_VERSION = "1.5.1"
ROCKBOX_UTILITY_WINDOWS_URL = (
    "https://download.rockbox.org/rbutil/win32/"
    "RockboxUtility-v1.5.1.zip"
)
ROCKBOX_UTILITY_WINDOWS_SIZE = 13_661_541
ROCKBOX_UTILITY_WINDOWS_SHA256 = (
    "3226b5ede00bd7d7a0458af4f5428b8080c7983650e14087b6b4050d6a23c46d"
)
ROCKBOX_UTILITY_SOURCE_URL = (
    "https://download.rockbox.org/rbutil/source/"
    "RockboxUtility-v1.5.1-src.tar.bz2"
)
ROCKBOX_UTILITY_SOURCE_SIZE = 1_495_776
ROCKBOX_UTILITY_SOURCE_SHA256 = (
    "82e34ed756b4777d117b13c400040622057d5b5ef38138d9fcb373fe8527e073"
)

OLSRO_GUIDE_REVISION = "1f3d33805259c1c2b58a5076bb3580e86bacdaf1"
ROCKBOX_HELPER_REPOSITORY = "https://git.rockbox.org/cgit/rockbox.git"
ROCKBOX_HELPER_COMMIT = "2df1172e985c45e9bf7fe3283bbb42dfaa36c735"
VELA_READBACK_PATCH_SHA256 = (
    "321a42df1247d05acc1332468b4afb845ede9c304694a60e1380f1a63deab405"
)
MAX_ARTIFACT_SIZE = 128 * 1024 * 1024
MAX_HELPER_SOURCE_SIZE = 512 * 1024 * 1024
MAX_BUILD_MANIFEST_SIZE = 64 * 1024
DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_TIMEOUT_SECONDS = 120.0
DEFAULT_CHUNK_SIZE = 1024 * 1024

_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,79}$")


class ArtifactError(RuntimeError):
    """Base artifact failure with a stable code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ArtifactMetadataError(ArtifactError):
    """Pinned metadata is malformed or unsafe."""


class ArtifactValidationError(ArtifactError):
    """Artifact bytes do not exactly match the pin."""


class ArtifactDownloadError(ArtifactError):
    """A bounded explicit transfer failed before atomic installation."""


class ArtifactCancelled(ArtifactError):
    """The caller cancelled validation or transfer."""

    def __init__(self) -> None:
        super().__init__("cancelled", "Artifact processing was cancelled.")


@dataclasses.dataclass(frozen=True, slots=True)
class ArtifactSpec:
    artifact_id: str
    filename: str
    url: str
    expected_size: int
    sha256: str
    sha1: str | None
    kind: str
    license_expression: str
    source_revision: str
    redistributable: bool
    executable: bool = False

    def __post_init__(self) -> None:
        artifact_id = str(self.artifact_id or "").lower()
        if not _ARTIFACT_ID_RE.fullmatch(artifact_id):
            raise ArtifactMetadataError(
                "invalid_artifact_id", "The artifact ID is not a bounded stable identifier."
            )
        filename = str(self.filename or "")
        if (
            not filename
            or filename in {".", ".."}
            or Path(filename).name != filename
            or "/" in filename
            or "\\" in filename
        ):
            raise ArtifactMetadataError(
                "invalid_filename", "The artifact filename must be a plain basename."
            )
        parsed = urllib.parse.urlsplit(str(self.url or ""))
        if (
            parsed.scheme.lower() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise ArtifactMetadataError(
                "insecure_url", "Pinned artifacts must use an HTTPS URL without credentials."
            )
        if (
            isinstance(self.expected_size, bool)
            or not isinstance(self.expected_size, int)
            or not 0 < self.expected_size <= MAX_ARTIFACT_SIZE
        ):
            raise ArtifactMetadataError(
                "invalid_size", "The pinned artifact size is outside the allowed bound."
            )
        sha256 = str(self.sha256 or "").lower()
        sha1 = str(self.sha1 or "").lower() if self.sha1 is not None else None
        if not _SHA256_RE.fullmatch(sha256):
            raise ArtifactMetadataError(
                "invalid_sha256", "The pinned artifact SHA-256 is invalid."
            )
        if sha1 is not None and not _SHA1_RE.fullmatch(sha1):
            raise ArtifactMetadataError(
                "invalid_sha1", "The pinned artifact SHA-1 is invalid."
            )
        if not str(self.kind or "").strip():
            raise ArtifactMetadataError(
                "invalid_kind", "The pinned artifact kind is required."
            )
        if not str(self.license_expression or "").strip():
            raise ArtifactMetadataError(
                "missing_license", "The pinned artifact license metadata is required."
            )
        if not str(self.source_revision or "").strip():
            raise ArtifactMetadataError(
                "missing_revision", "The pinned artifact source revision is required."
            )
        object.__setattr__(self, "artifact_id", artifact_id)
        object.__setattr__(self, "sha256", sha256)
        object.__setattr__(self, "sha1", sha1)

    @property
    def metadata_sha256(self) -> str:
        value = {
            "artifact_id": self.artifact_id,
            "filename": self.filename,
            "url": self.url,
            "expected_size": self.expected_size,
            "sha256": self.sha256,
            "sha1": self.sha1,
            "kind": self.kind,
            "license_expression": self.license_expression,
            "source_revision": self.source_revision,
            "redistributable": self.redistributable,
            "executable": self.executable,
        }
        return hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def to_dto(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "filename": self.filename,
            "url": self.url,
            "expected_size": self.expected_size,
            "sha1": self.sha1,
            "sha256": self.sha256,
            "kind": self.kind,
            "license_expression": self.license_expression,
            "source_revision": self.source_revision,
            "redistributable": self.redistributable,
            "executable": self.executable,
            "metadata_sha256": self.metadata_sha256,
        }


APPLE_IPOD_CLASSIC_2_0_2_IPSW = ArtifactSpec(
    artifact_id="apple-ipod-classic-2.0.2-ipsw",
    filename="iPod_35.2.0.2.ipsw",
    url=APPLE_IPSW_URL,
    expected_size=APPLE_IPSW_SIZE,
    sha1=APPLE_IPSW_SHA1,
    sha256=APPLE_IPSW_SHA256,
    kind="apple-firmware",
    license_expression="Apple proprietary; download-only, no redistribution",
    source_revision="Apple 061-6797 (2009-09-09)",
    redistributable=False,
)

ROCKBOX_UTILITY_WINDOWS = ArtifactSpec(
    artifact_id="rockbox-utility-1.5.1-windows",
    filename="RockboxUtility-v1.5.1.zip",
    url=ROCKBOX_UTILITY_WINDOWS_URL,
    expected_size=ROCKBOX_UTILITY_WINDOWS_SIZE,
    sha1=None,
    sha256=ROCKBOX_UTILITY_WINDOWS_SHA256,
    kind="bootloader-tool",
    license_expression="GPL-2.0-or-later",
    source_revision="Rockbox Utility 1.5.1 (2022-04-18)",
    redistributable=True,
    executable=True,
)

ROCKBOX_UTILITY_SOURCE = ArtifactSpec(
    artifact_id="rockbox-utility-1.5.1-source",
    filename="RockboxUtility-v1.5.1-src.tar.bz2",
    url=ROCKBOX_UTILITY_SOURCE_URL,
    expected_size=ROCKBOX_UTILITY_SOURCE_SIZE,
    sha1=None,
    sha256=ROCKBOX_UTILITY_SOURCE_SHA256,
    kind="corresponding-source",
    license_expression="GPL-2.0-or-later",
    source_revision=(
        "Rockbox Utility 1.5.1 source; includes utils/mks5lboot"
    ),
    redistributable=True,
)

OLSRO_SYSCFG_EDITOR_SOURCE = ArtifactSpec(
    artifact_id="olsro-syscfg-editor-source",
    filename="iPodSysCFGEditor-SourcesOnlyForDevs.7z",
    url=(
        "https://raw.githubusercontent.com/Olsro/reddit-ipod-guides/"
        f"{OLSRO_GUIDE_REVISION}/guides/ipod6g-flash-more-recent-firmwares/"
        "iPodSysCFGEditor-SourcesOnlyForDevs.7z"
    ),
    expected_size=106_847,
    sha1=None,
    sha256="2e51d5e8fcf4ac36d42222869a3edb77f1527a4c34d326fb112be37b46c31017",
    kind="source-only-reference",
    license_expression="MIT repository; contents require source audit",
    source_revision=OLSRO_GUIDE_REVISION,
    redistributable=True,
)

OLSRO_ROCKBOX_SYSCFG_PATCH = ArtifactSpec(
    artifact_id="olsro-rockbox-syscfg-patch",
    filename="rockbox-2df1172-ipod6g-syscfg.patch",
    url=(
        "https://raw.githubusercontent.com/Olsro/reddit-ipod-guides/"
        f"{OLSRO_GUIDE_REVISION}/guides/ipod6g-flash-more-recent-firmwares/"
        "rockbox-2df1172-ipod6g%3A%20add%20SysCFG%20flashing%20tools%20"
        "from%20the%20debug%20menu.patch"
    ),
    expected_size=5_827,
    sha1=None,
    sha256="6eb01128105d875d24db8828f1b4f73250279527fb71d161c42aee1e5924feac",
    kind="source-patch-reference",
    license_expression="GPL-2.0-or-later",
    source_revision=OLSRO_GUIDE_REVISION,
    redistributable=True,
)

PINNED_UNLOCK_ARTIFACTS: Mapping[str, ArtifactSpec] = MappingProxyType(
    {
        artifact.artifact_id: artifact
        for artifact in (
            APPLE_IPOD_CLASSIC_2_0_2_IPSW,
            ROCKBOX_UTILITY_WINDOWS,
            ROCKBOX_UTILITY_SOURCE,
            OLSRO_SYSCFG_EDITOR_SOURCE,
            OLSRO_ROCKBOX_SYSCFG_PATCH,
        )
    }
)
REQUIRED_UNLOCK_ARTIFACT_IDS = frozenset(PINNED_UNLOCK_ARTIFACTS)
HELPER_BUILD_LOCK: Mapping[str, str] = MappingProxyType(
    {
        "Rockbox repository": ROCKBOX_HELPER_REPOSITORY,
        "Rockbox commit": ROCKBOX_HELPER_COMMIT,
        "Target": "ipod6g",
        "Olsro guide revision": OLSRO_GUIDE_REVISION,
        "Olsro patch SHA-256": OLSRO_ROCKBOX_SYSCFG_PATCH.sha256,
        "Vela readback patch SHA-256": VELA_READBACK_PATCH_SHA256,
        "License": "GPL-2.0-or-later",
    }
)
HELPER_BUILD_LOCK_SHA256 = hashlib.sha256(
    json.dumps(
        dict(HELPER_BUILD_LOCK),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()
_HELPER_ARCHIVE_NAME = "vela-ipod6g-syscfg-helper.zip"
_HELPER_SOURCE_NAME = "vela-ipod6g-helper-corresponding-source.tar.gz"
_HELPER_MANIFEST_NAME = "BUILD-MANIFEST.txt"
_REQUIRED_HELPER_SOURCE_PATHS = frozenset(
    {
        "artifact-lock.json",
        "build-rockbox-helper.sh",
        "README.md",
        "GPL-2.0-or-later.txt",
        "Olsro-MIT.txt",
        "olsro-syscfg.patch",
        "vela-rockbox-syscfg-readback.patch",
        "rockbox/apps/debug_menu.c",
        "rockbox/firmware/target/arm/s5l8702/norboot-s5l8702.c",
    }
)
_REQUIRED_HELPER_SOURCE_SHA256: Mapping[str, str] = MappingProxyType(
    {
        "olsro-syscfg.patch": OLSRO_ROCKBOX_SYSCFG_PATCH.sha256,
        "vela-rockbox-syscfg-readback.patch": VELA_READBACK_PATCH_SHA256,
    }
)
_MAX_EMBEDDED_PATCH_SIZE = 1024 * 1024


@dataclasses.dataclass(frozen=True, slots=True)
class ArtifactProgress:
    artifact_id: str
    stage: str
    current_bytes: int
    total_bytes: int


@dataclasses.dataclass(frozen=True, slots=True)
class ArtifactReceipt:
    artifact_id: str
    path: str
    size: int
    sha1: str | None
    sha256: str
    metadata_sha256: str

    def to_dto(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True, slots=True)
class HelperBuildReceipt:
    helper_filename: str
    helper_size: int
    helper_sha256: str
    source_filename: str
    source_size: int
    source_sha256: str
    manifest_filename: str
    manifest_sha256: str
    compiler: str
    lock_fingerprint: str
    provenance: str = "validated-build-manifest-v1"

    def __post_init__(self) -> None:
        if (
            self.helper_filename != _HELPER_ARCHIVE_NAME
            or self.source_filename != _HELPER_SOURCE_NAME
            or self.manifest_filename != _HELPER_MANIFEST_NAME
        ):
            raise ArtifactMetadataError(
                "invalid_helper_receipt",
                "The helper build receipt contains an unexpected filename.",
            )
        if (
            isinstance(self.helper_size, bool)
            or not isinstance(self.helper_size, int)
            or not 0 < self.helper_size <= MAX_ARTIFACT_SIZE
            or isinstance(self.source_size, bool)
            or not isinstance(self.source_size, int)
            or not 0 < self.source_size <= MAX_HELPER_SOURCE_SIZE
        ):
            raise ArtifactMetadataError(
                "invalid_helper_receipt",
                "The helper build receipt contains an invalid size.",
            )
        for value in (
            self.helper_sha256,
            self.source_sha256,
            self.manifest_sha256,
        ):
            if not _SHA256_RE.fullmatch(str(value or "").casefold()):
                raise ArtifactMetadataError(
                    "invalid_helper_receipt",
                    "The helper build receipt contains an invalid SHA-256.",
                )
        if (
            self.lock_fingerprint != HELPER_BUILD_LOCK_SHA256
            or self.provenance != "validated-build-manifest-v1"
            or not str(self.compiler or "").strip()
            or len(str(self.compiler)) > 500
        ):
            raise ArtifactMetadataError(
                "invalid_helper_receipt",
                "The helper build receipt does not match the audited build lock.",
            )

    def to_redacted_dto(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


ProgressCallback = Callable[[ArtifactProgress], None]
CancellationCallback = Callable[[], bool]


class ArtifactResponse(Protocol):
    headers: Mapping[str, str]

    def read(self, size: int = -1) -> bytes:
        ...

    def geturl(self) -> str:
        ...


class ArtifactOpener(Protocol):
    def __call__(
        self, request: urllib.request.Request, timeout: float
    ) -> AbstractContextManager[ArtifactResponse]:
        ...


def _check_cancelled(cancelled: CancellationCallback | None) -> None:
    if cancelled is not None and bool(cancelled()):
        raise ArtifactCancelled()


def _emit_progress(
    callback: ProgressCallback | None,
    spec: ArtifactSpec,
    stage: str,
    current: int,
) -> None:
    if callback is not None:
        callback(
            ArtifactProgress(
                artifact_id=spec.artifact_id,
                stage=stage,
                current_bytes=current,
                total_bytes=spec.expected_size,
            )
        )


def _stream_and_hash(
    stream: BinaryIO,
    spec: ArtifactSpec,
    *,
    output: BinaryIO | None,
    stage: str,
    progress: ProgressCallback | None,
    cancelled: CancellationCallback | None,
    chunk_size: int,
) -> tuple[int, str | None, str]:
    if (
        isinstance(chunk_size, bool)
        or not isinstance(chunk_size, int)
        or not 4096 <= chunk_size <= 8 * 1024 * 1024
    ):
        raise ArtifactValidationError(
            "invalid_chunk_size", "The streaming chunk size is outside the safe bound."
        )
    sha1_hasher = hashlib.sha1() if spec.sha1 is not None else None
    sha256_hasher = hashlib.sha256()
    total = 0
    _check_cancelled(cancelled)
    _emit_progress(progress, spec, stage, total)
    while True:
        _check_cancelled(cancelled)
        chunk = stream.read(chunk_size)
        if chunk is None:
            raise ArtifactValidationError(
                "invalid_stream", "The artifact stream returned no byte value."
            )
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise ArtifactValidationError(
                "invalid_stream", "The artifact stream returned non-byte data."
            )
        chunk_bytes = bytes(chunk)
        if not chunk_bytes:
            break
        total += len(chunk_bytes)
        if total > spec.expected_size or total > MAX_ARTIFACT_SIZE:
            raise ArtifactValidationError(
                "artifact_too_large", "The artifact exceeds its exact pinned size."
            )
        if output is not None:
            output.write(chunk_bytes)
        if sha1_hasher is not None:
            sha1_hasher.update(chunk_bytes)
        sha256_hasher.update(chunk_bytes)
        _emit_progress(progress, spec, stage, total)
    _check_cancelled(cancelled)
    if total != spec.expected_size:
        raise ArtifactValidationError(
            "size_mismatch", "The artifact size does not match its exact pin."
        )
    actual_sha1 = sha1_hasher.hexdigest() if sha1_hasher is not None else None
    actual_sha256 = sha256_hasher.hexdigest()
    if spec.sha1 is not None and actual_sha1 != spec.sha1:
        raise ArtifactValidationError(
            "sha1_mismatch", "The artifact SHA-1 does not match its exact pin."
        )
    if actual_sha256 != spec.sha256:
        raise ArtifactValidationError(
            "sha256_mismatch", "The artifact SHA-256 does not match its exact pin."
        )
    _emit_progress(progress, spec, f"{stage}:verified", total)
    return total, actual_sha1, actual_sha256


def validate_artifact_file(
    path: str | os.PathLike[str],
    spec: ArtifactSpec,
    *,
    progress: ProgressCallback | None = None,
    cancelled: CancellationCallback | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> ArtifactReceipt:
    """Stream-validate one local file against exact pinned size and hashes."""

    candidate = Path(path)
    try:
        before = candidate.lstat()
    except OSError as exc:
        raise ArtifactValidationError(
            "artifact_unreadable", "The selected artifact file cannot be read."
        ) from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise ArtifactValidationError(
            "artifact_not_regular", "The selected artifact must be a regular non-symlink file."
        )
    if before.st_size != spec.expected_size:
        raise ArtifactValidationError(
            "size_mismatch", "The artifact size does not match its exact pin."
        )
    try:
        with candidate.open("rb") as source:
            size, actual_sha1, actual_sha256 = _stream_and_hash(
                source,
                spec,
                output=None,
                stage="validating",
                progress=progress,
                cancelled=cancelled,
                chunk_size=chunk_size,
            )
            after = os.fstat(source.fileno())
    except ArtifactError:
        raise
    except OSError as exc:
        raise ArtifactValidationError(
            "artifact_unreadable", "The selected artifact could not be validated."
        ) from exc
    if (
        after.st_size != before.st_size
        or after.st_mtime_ns != before.st_mtime_ns
        or (
            before.st_ino
            and after.st_ino
            and (after.st_ino != before.st_ino or after.st_dev != before.st_dev)
        )
    ):
        raise ArtifactValidationError(
            "artifact_changed", "The artifact changed while it was being validated."
        )
    return ArtifactReceipt(
        artifact_id=spec.artifact_id,
        path=str(candidate.resolve()),
        size=size,
        sha1=actual_sha1,
        sha256=actual_sha256,
        metadata_sha256=spec.metadata_sha256,
    )


def _hash_build_file(
    path: str | os.PathLike[str],
    *,
    expected_name: str,
    max_size: int,
    cancelled: CancellationCallback | None,
    capture: bool = False,
) -> tuple[Path, int, str, bytes | None]:
    candidate = Path(path)
    try:
        before = candidate.lstat()
    except OSError as exc:
        raise ArtifactValidationError(
            "helper_file_unreadable",
            f"The selected {expected_name} file cannot be read.",
        ) from exc
    if (
        candidate.name != expected_name
        or stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
    ):
        raise ArtifactValidationError(
            "invalid_helper_file",
            f"Select the regular non-symlink file named {expected_name}.",
        )
    if before.st_size <= 0 or before.st_size > max_size:
        raise ArtifactValidationError(
            "invalid_helper_file_size",
            f"The selected {expected_name} file is outside its safe size bound.",
        )

    digest = hashlib.sha256()
    collected = bytearray() if capture else None
    total = 0
    try:
        with candidate.open("rb") as source:
            while True:
                _check_cancelled(cancelled)
                chunk = source.read(DEFAULT_CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_size:
                    raise ArtifactValidationError(
                        "invalid_helper_file_size",
                        f"The selected {expected_name} file exceeds its safe size bound.",
                    )
                digest.update(chunk)
                if collected is not None:
                    collected.extend(chunk)
            after = os.fstat(source.fileno())
    except ArtifactError:
        raise
    except OSError as exc:
        raise ArtifactValidationError(
            "helper_file_unreadable",
            f"The selected {expected_name} file could not be verified.",
        ) from exc
    if (
        total != before.st_size
        or after.st_size != before.st_size
        or after.st_mtime_ns != before.st_mtime_ns
        or (
            before.st_ino
            and after.st_ino
            and (after.st_ino != before.st_ino or after.st_dev != before.st_dev)
        )
    ):
        raise ArtifactValidationError(
            "helper_file_changed",
            f"The selected {expected_name} file changed during verification.",
        )
    _check_cancelled(cancelled)
    return (
        candidate.resolve(),
        total,
        digest.hexdigest(),
        bytes(collected) if collected is not None else None,
    )


def _normalized_archive_member(name: str) -> str:
    if not isinstance(name, str) or not name or "\\" in name or name.startswith("/"):
        raise ArtifactValidationError(
            "invalid_helper_archive",
            "The helper build archive contains an unsafe path.",
        )
    parts = [part for part in name.split("/") if part not in {"", "."}]
    if any(part == ".." or "\x00" in part for part in parts):
        raise ArtifactValidationError(
            "invalid_helper_archive",
            "The helper build archive contains an unsafe path.",
        )
    return "/".join(parts)


def _validate_helper_archive(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if not members or len(members) > 20_000:
                raise ArtifactValidationError(
                    "invalid_helper_archive",
                    "The helper archive has an invalid file count.",
                )
            names: set[str] = set()
            expanded_size = 0
            for member in members:
                normalized = _normalized_archive_member(member.filename)
                if not normalized:
                    continue
                if normalized in names:
                    raise ArtifactValidationError(
                        "invalid_helper_archive",
                        "The helper archive contains duplicate paths.",
                    )
                names.add(normalized)
                if member.flag_bits & 0x1:
                    raise ArtifactValidationError(
                        "invalid_helper_archive",
                        "The helper archive contains an encrypted entry.",
                    )
                expanded_size += int(member.file_size)
                if expanded_size > 2 * 1024 * 1024 * 1024:
                    raise ArtifactValidationError(
                        "invalid_helper_archive",
                        "The helper archive expands beyond the safe size bound.",
                    )
            if ".rockbox/rockbox.ipod" not in names:
                raise ArtifactValidationError(
                    "invalid_helper_archive",
                    "The helper archive does not contain the built iPod 6G firmware.",
                )
    except ArtifactError:
        raise
    except (OSError, zipfile.BadZipFile, ValueError) as exc:
        raise ArtifactValidationError(
            "invalid_helper_archive",
            "The selected helper output is not a readable ZIP archive.",
        ) from exc


def _validate_helper_source_archive(
    path: Path,
    *,
    cancelled: CancellationCallback | None = None,
) -> None:
    try:
        with tarfile.open(path, mode="r:gz") as archive:
            members = archive.getmembers()
            if not members or len(members) > 250_000:
                raise ArtifactValidationError(
                    "invalid_helper_source",
                    "The corresponding-source archive has an invalid file count.",
                )
            by_name: dict[str, tarfile.TarInfo] = {}
            for member in members:
                normalized = _normalized_archive_member(member.name)
                if not normalized:
                    continue
                if normalized in by_name:
                    raise ArtifactValidationError(
                        "invalid_helper_source",
                        "The corresponding-source archive contains duplicate paths.",
                    )
                by_name[normalized] = member
            missing = sorted(_REQUIRED_HELPER_SOURCE_PATHS - set(by_name))
            if missing or any(
                not by_name[name].isfile()
                for name in _REQUIRED_HELPER_SOURCE_PATHS
                if name in by_name
            ):
                raise ArtifactValidationError(
                    "incomplete_helper_source",
                    "The corresponding-source archive is missing audited source, "
                    "patch, recipe, or license files.",
                )
            for name, expected_sha256 in _REQUIRED_HELPER_SOURCE_SHA256.items():
                member = by_name[name]
                if member.size <= 0 or member.size > _MAX_EMBEDDED_PATCH_SIZE:
                    raise ArtifactValidationError(
                        "invalid_helper_source",
                        f"The corresponding-source archive contains an invalid {name}.",
                    )
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise ArtifactValidationError(
                        "incomplete_helper_source",
                        f"The corresponding-source archive cannot read {name}.",
                    )
                digest = hashlib.sha256()
                with extracted:
                    while True:
                        _check_cancelled(cancelled)
                        chunk = extracted.read(DEFAULT_CHUNK_SIZE)
                        if not chunk:
                            break
                        digest.update(chunk)
                if digest.hexdigest() != expected_sha256:
                    raise ArtifactValidationError(
                        "helper_source_patch_mismatch",
                        f"The corresponding-source archive contains an unaudited {name}.",
                    )
    except ArtifactError:
        raise
    except (OSError, tarfile.TarError, ValueError) as exc:
        raise ArtifactValidationError(
            "invalid_helper_source",
            "The corresponding-source output is not a readable gzip tar archive.",
        ) from exc


def _parse_helper_build_manifest(raw: bytes) -> dict[str, str]:
    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise ArtifactValidationError(
            "invalid_helper_manifest",
            "BUILD-MANIFEST.txt must be valid UTF-8 text.",
        ) from exc
    if not text.endswith("\n"):
        raise ArtifactValidationError(
            "invalid_helper_manifest",
            "BUILD-MANIFEST.txt is incomplete.",
        )
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        if not raw_line or len(raw_line) > 1_000 or ": " not in raw_line:
            raise ArtifactValidationError(
                "invalid_helper_manifest",
                "BUILD-MANIFEST.txt contains an invalid line.",
            )
        key, value = raw_line.split(": ", 1)
        if (
            key in values
            or not key
            or not value
            or key.strip() != key
            or value.strip() != value
        ):
            raise ArtifactValidationError(
                "invalid_helper_manifest",
                "BUILD-MANIFEST.txt contains duplicate or ambiguous fields.",
            )
        values[key] = value
    expected_keys = {
        *HELPER_BUILD_LOCK,
        "Helper archive SHA-256",
        "Corresponding source SHA-256",
        "Compiler",
    }
    if set(values) != expected_keys:
        raise ArtifactValidationError(
            "invalid_helper_manifest",
            "BUILD-MANIFEST.txt does not contain the exact audited field set.",
        )
    for key, expected in HELPER_BUILD_LOCK.items():
        if values.get(key) != expected:
            raise ArtifactValidationError(
                "helper_build_lock_mismatch",
                f"BUILD-MANIFEST.txt does not match the audited {key}.",
            )
    for key in ("Helper archive SHA-256", "Corresponding source SHA-256"):
        if not _SHA256_RE.fullmatch(values[key].casefold()):
            raise ArtifactValidationError(
                "invalid_helper_manifest",
                f"BUILD-MANIFEST.txt contains an invalid {key}.",
            )
    if len(values["Compiler"]) > 500:
        raise ArtifactValidationError(
            "invalid_helper_manifest",
            "BUILD-MANIFEST.txt contains an invalid compiler description.",
        )
    return values


def validate_rockbox_helper_build(
    helper_path: str | os.PathLike[str],
    source_path: str | os.PathLike[str],
    manifest_path: str | os.PathLike[str],
    *,
    cancelled: CancellationCallback | None = None,
) -> HelperBuildReceipt:
    """Verify a local helper, corresponding source, and locked build manifest."""
    helper, helper_size, helper_sha256, _ = _hash_build_file(
        helper_path,
        expected_name=_HELPER_ARCHIVE_NAME,
        max_size=MAX_ARTIFACT_SIZE,
        cancelled=cancelled,
    )
    source, source_size, source_sha256, _ = _hash_build_file(
        source_path,
        expected_name=_HELPER_SOURCE_NAME,
        max_size=MAX_HELPER_SOURCE_SIZE,
        cancelled=cancelled,
    )
    _manifest, _manifest_size, manifest_sha256, manifest_raw = _hash_build_file(
        manifest_path,
        expected_name=_HELPER_MANIFEST_NAME,
        max_size=MAX_BUILD_MANIFEST_SIZE,
        cancelled=cancelled,
        capture=True,
    )
    if manifest_raw is None:
        raise ArtifactValidationError(
            "invalid_helper_manifest",
            "BUILD-MANIFEST.txt could not be read.",
        )
    values = _parse_helper_build_manifest(manifest_raw)
    if values["Helper archive SHA-256"].casefold() != helper_sha256:
        raise ArtifactValidationError(
            "helper_hash_mismatch",
            "The helper ZIP does not match BUILD-MANIFEST.txt.",
        )
    if values["Corresponding source SHA-256"].casefold() != source_sha256:
        raise ArtifactValidationError(
            "helper_source_hash_mismatch",
            "The corresponding-source archive does not match BUILD-MANIFEST.txt.",
        )
    _validate_helper_archive(helper)
    _validate_helper_source_archive(source, cancelled=cancelled)
    return HelperBuildReceipt(
        helper_filename=helper.name,
        helper_size=helper_size,
        helper_sha256=helper_sha256,
        source_filename=source.name,
        source_size=source_size,
        source_sha256=source_sha256,
        manifest_filename=_HELPER_MANIFEST_NAME,
        manifest_sha256=manifest_sha256,
        compiler=values["Compiler"],
        lock_fingerprint=HELPER_BUILD_LOCK_SHA256,
    )


def _default_opener(
    request: urllib.request.Request, timeout: float
) -> AbstractContextManager[ArtifactResponse]:
    return urllib.request.urlopen(request, timeout=timeout)  # type: ignore[return-value]


def _validate_timeout(timeout: float) -> float:
    try:
        value = float(timeout)
    except (TypeError, ValueError) as exc:
        raise ArtifactDownloadError(
            "invalid_timeout", "The artifact timeout is invalid."
        ) from exc
    if not 1.0 <= value <= MAX_TIMEOUT_SECONDS:
        raise ArtifactDownloadError(
            "invalid_timeout", "The artifact timeout is outside the bounded range."
        )
    return value


def _content_length(response: ArtifactResponse) -> int | None:
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    raw = headers.get("Content-Length")
    if raw in (None, ""):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ArtifactDownloadError(
            "invalid_content_length", "The server returned an invalid Content-Length."
        ) from exc
    if value < 0:
        raise ArtifactDownloadError(
            "invalid_content_length", "The server returned an invalid Content-Length."
        )
    return value


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY)
        os.fsync(descriptor)
    except OSError:
        # The file itself was already fsynced.  Some filesystems do not permit
        # directory fsync, so this is best-effort portability hardening.
        return
    finally:
        if descriptor is not None:
            os.close(descriptor)


class ArtifactDownloader:
    """Injectable HTTPS downloader used only after an explicit caller action."""

    def __init__(self, opener: ArtifactOpener | None = None):
        self._opener = opener or _default_opener

    def download(
        self,
        spec: ArtifactSpec,
        destination: str | os.PathLike[str],
        *,
        explicit_user_action: bool,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        progress: ProgressCallback | None = None,
        cancelled: CancellationCallback | None = None,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ) -> ArtifactReceipt:
        if explicit_user_action is not True:
            raise ArtifactDownloadError(
                "explicit_action_required",
                "Artifact download requires an explicit caller-confirmed action.",
            )
        timeout_value = _validate_timeout(timeout)
        parsed = urllib.parse.urlsplit(spec.url)
        if parsed.scheme.lower() != "https":
            raise ArtifactDownloadError(
                "insecure_url", "Artifact downloads require HTTPS."
            )

        target = Path(destination)
        parent = target.parent
        if not parent.is_dir():
            raise ArtifactDownloadError(
                "destination_parent_missing",
                "The artifact destination directory does not exist.",
            )
        if target.exists() or target.is_symlink():
            try:
                target_status = target.lstat()
            except OSError as exc:
                raise ArtifactDownloadError(
                    "destination_unreadable",
                    "The artifact destination could not be inspected.",
                ) from exc
            if stat.S_ISLNK(target_status.st_mode) or not stat.S_ISREG(
                target_status.st_mode
            ):
                raise ArtifactDownloadError(
                    "unsafe_destination",
                    "The artifact destination must be a regular non-symlink file.",
                )

        request = urllib.request.Request(
            spec.url,
            headers={
                "Accept": "application/octet-stream",
                "Accept-Encoding": "identity",
                "User-Agent": "Vela-iPod-Unlock/1",
            },
            method="GET",
        )
        temporary_path: Path | None = None
        try:
            _check_cancelled(cancelled)
            with self._opener(request, timeout_value) as response:
                final_url = (
                    response.geturl()
                    if callable(getattr(response, "geturl", None))
                    else spec.url
                )
                if urllib.parse.urlsplit(final_url).scheme.lower() != "https":
                    raise ArtifactDownloadError(
                        "insecure_redirect",
                        "The artifact server redirected to a non-HTTPS URL.",
                    )
                length = _content_length(response)
                if length is not None and length != spec.expected_size:
                    raise ArtifactValidationError(
                        "size_mismatch",
                        "The server Content-Length does not match the exact pin.",
                    )
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    dir=parent,
                    prefix=f".{target.name}.",
                    suffix=".part",
                    delete=False,
                ) as temporary:
                    temporary_path = Path(temporary.name)
                    size, actual_sha1, actual_sha256 = _stream_and_hash(
                        response,  # type: ignore[arg-type]
                        spec,
                        output=temporary,
                        stage="downloading",
                        progress=progress,
                        cancelled=cancelled,
                        chunk_size=chunk_size,
                    )
                    temporary.flush()
                    os.fsync(temporary.fileno())
            _check_cancelled(cancelled)
            os.replace(temporary_path, target)
            temporary_path = None
            _fsync_directory(parent)
            return ArtifactReceipt(
                artifact_id=spec.artifact_id,
                path=str(target.resolve()),
                size=size,
                sha1=actual_sha1,
                sha256=actual_sha256,
                metadata_sha256=spec.metadata_sha256,
            )
        except ArtifactError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ArtifactDownloadError(
                "download_failed", "The artifact download failed before installation."
            ) from exc
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass


def download_artifact(
    spec: ArtifactSpec,
    destination: str | os.PathLike[str],
    *,
    explicit_user_action: bool,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    progress: ProgressCallback | None = None,
    cancelled: CancellationCallback | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    opener: ArtifactOpener | None = None,
) -> ArtifactReceipt:
    """Convenience wrapper around :class:`ArtifactDownloader`."""

    return ArtifactDownloader(opener=opener).download(
        spec,
        destination,
        explicit_user_action=explicit_user_action,
        timeout=timeout,
        progress=progress,
        cancelled=cancelled,
        chunk_size=chunk_size,
    )
