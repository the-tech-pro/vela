from __future__ import annotations

import hashlib
import io
import tarfile
import zipfile
from pathlib import Path

import pytest

import antra.core.ipod_unlock_artifacts as artifacts
from antra.core.ipod_unlock_artifacts import (
    APPLE_IPOD_CLASSIC_2_0_2_IPSW,
    APPLE_IPSW_SHA1,
    APPLE_IPSW_SHA256,
    APPLE_IPSW_SIZE,
    APPLE_IPSW_URL,
    HELPER_BUILD_LOCK,
    HELPER_BUILD_LOCK_SHA256,
    OLSRO_ROCKBOX_SYSCFG_PATCH,
    OLSRO_SYSCFG_EDITOR_SOURCE,
    ROCKBOX_UTILITY_SOURCE,
    ROCKBOX_UTILITY_SOURCE_SHA256,
    ROCKBOX_UTILITY_WINDOWS,
    ROCKBOX_UTILITY_WINDOWS_SHA256,
    ArtifactCancelled,
    ArtifactDownloadError,
    ArtifactDownloader,
    ArtifactMetadataError,
    ArtifactSpec,
    ArtifactValidationError,
    validate_artifact_file,
    validate_rockbox_helper_build,
)


def make_spec(payload: bytes, *, artifact_id: str = "synthetic-artifact") -> ArtifactSpec:
    return ArtifactSpec(
        artifact_id=artifact_id,
        filename=f"{artifact_id}.bin",
        url=f"https://fixtures.invalid/{artifact_id}.bin",
        expected_size=len(payload),
        sha1=hashlib.sha1(payload).hexdigest(),
        sha256=hashlib.sha256(payload).hexdigest(),
        kind="synthetic-test",
        license_expression="test-only",
        source_revision="synthetic",
        redistributable=False,
    )


class FakeResponse(io.BytesIO):
    def __init__(
        self,
        payload: bytes,
        *,
        url: str = "https://fixtures.invalid/final.bin",
        content_length: int | None = None,
    ):
        super().__init__(payload)
        self._url = url
        self.headers = (
            {}
            if content_length is None
            else {"Content-Length": str(content_length)}
        )

    def geturl(self) -> str:
        return self._url


class FakeOpener:
    def __init__(
        self,
        payload: bytes,
        *,
        final_url: str = "https://fixtures.invalid/final.bin",
        content_length: int | None = None,
    ):
        self.payload = payload
        self.final_url = final_url
        self.content_length = content_length
        self.calls = []

    def __call__(self, request, timeout):
        self.calls.append((request, timeout))
        return FakeResponse(
            self.payload,
            url=self.final_url,
            content_length=self.content_length,
        )


def test_apple_and_open_source_metadata_are_exactly_pinned() -> None:
    assert APPLE_IPOD_CLASSIC_2_0_2_IPSW.url == APPLE_IPSW_URL
    assert APPLE_IPOD_CLASSIC_2_0_2_IPSW.expected_size == APPLE_IPSW_SIZE
    assert APPLE_IPSW_SIZE == 61_033_067
    assert APPLE_IPOD_CLASSIC_2_0_2_IPSW.sha1 == APPLE_IPSW_SHA1
    assert APPLE_IPSW_SHA1 == "5a7ab72fd7e299118bb0f25adfea1ad4808e1f0a"
    assert APPLE_IPOD_CLASSIC_2_0_2_IPSW.sha256 == APPLE_IPSW_SHA256
    assert (
        APPLE_IPSW_SHA256
        == "a12f25067a821850979efe8222de6e2bb98eba985ba21f61abe386355c6655b4"
    )
    assert APPLE_IPOD_CLASSIC_2_0_2_IPSW.redistributable is False

    assert ROCKBOX_UTILITY_WINDOWS.expected_size == 13_661_541
    assert ROCKBOX_UTILITY_WINDOWS.sha256 == ROCKBOX_UTILITY_WINDOWS_SHA256
    assert (
        ROCKBOX_UTILITY_WINDOWS_SHA256
        == "3226b5ede00bd7d7a0458af4f5428b8080c7983650e14087b6b4050d6a23c46d"
    )
    assert ROCKBOX_UTILITY_WINDOWS.executable is True
    assert ROCKBOX_UTILITY_SOURCE.expected_size == 1_495_776
    assert ROCKBOX_UTILITY_SOURCE.sha256 == ROCKBOX_UTILITY_SOURCE_SHA256
    assert (
        ROCKBOX_UTILITY_SOURCE_SHA256
        == "82e34ed756b4777d117b13c400040622057d5b5ef38138d9fcb373fe8527e073"
    )
    assert "mks5lboot" in ROCKBOX_UTILITY_SOURCE.source_revision

    assert OLSRO_SYSCFG_EDITOR_SOURCE.expected_size == 106_847
    assert (
        OLSRO_SYSCFG_EDITOR_SOURCE.sha256
        == "2e51d5e8fcf4ac36d42222869a3edb77f1527a4c34d326fb112be37b46c31017"
    )
    assert OLSRO_ROCKBOX_SYSCFG_PATCH.expected_size == 5_827
    assert (
        OLSRO_ROCKBOX_SYSCFG_PATCH.sha256
        == "6eb01128105d875d24db8828f1b4f73250279527fb71d161c42aee1e5924feac"
    )
    assert "1f3d33805259c1c2b58a5076bb3580e86bacdaf1" in (
        OLSRO_ROCKBOX_SYSCFG_PATCH.url
    )


def test_helper_build_requires_matching_outputs_source_and_locked_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    helper = tmp_path / "vela-ipod6g-syscfg-helper.zip"
    with zipfile.ZipFile(helper, mode="w") as archive:
        archive.writestr(".rockbox/rockbox.ipod", b"synthetic firmware")

    source = tmp_path / "vela-ipod6g-helper-corresponding-source.tar.gz"
    required_source_paths = (
        "artifact-lock.json",
        "build-rockbox-helper.sh",
        "README.md",
        "GPL-2.0-or-later.txt",
        "Olsro-MIT.txt",
        "olsro-syscfg.patch",
        "vela-rockbox-syscfg-readback.patch",
        "rockbox/apps/debug_menu.c",
        "rockbox/firmware/target/arm/s5l8702/norboot-s5l8702.c",
    )
    embedded_patch_payloads = {
        "olsro-syscfg.patch": b"synthetic pinned Olsro patch\n",
        "vela-rockbox-syscfg-readback.patch": b"synthetic pinned Vela patch\n",
    }
    monkeypatch.setattr(
        artifacts,
        "_REQUIRED_HELPER_SOURCE_SHA256",
        {
            name: hashlib.sha256(payload).hexdigest()
            for name, payload in embedded_patch_payloads.items()
        },
    )

    def write_source(*, tamper_olsro_patch: bool = False) -> str:
        with tarfile.open(source, mode="w:gz") as archive:
            for name in required_source_paths:
                payload = embedded_patch_payloads.get(
                    name, f"synthetic {name}\n".encode()
                )
                if name == "olsro-syscfg.patch" and tamper_olsro_patch:
                    payload += b"tampered"
                member = tarfile.TarInfo(name)
                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))
        return hashlib.sha256(source.read_bytes()).hexdigest()

    def write_manifest(source_sha256: str) -> None:
        manifest.write_text(
            "".join(
                [
                    *(f"{key}: {value}\n" for key, value in HELPER_BUILD_LOCK.items()),
                    f"Helper archive SHA-256: {helper_sha256}\n",
                    f"Corresponding source SHA-256: {source_sha256}\n",
                    "Compiler: arm-elf-eabi-gcc synthetic-test\n",
                ]
            ),
            encoding="utf-8",
        )

    helper_sha256 = hashlib.sha256(helper.read_bytes()).hexdigest()
    manifest = tmp_path / "BUILD-MANIFEST.txt"
    source_sha256 = write_source()
    write_manifest(source_sha256)

    receipt = validate_rockbox_helper_build(helper, source, manifest)
    assert receipt.helper_sha256 == helper_sha256
    assert receipt.source_sha256 == source_sha256
    assert receipt.lock_fingerprint == HELPER_BUILD_LOCK_SHA256
    assert receipt.provenance == "validated-build-manifest-v1"

    tampered_source_sha256 = write_source(tamper_olsro_patch=True)
    write_manifest(tampered_source_sha256)
    with pytest.raises(ArtifactValidationError) as source_tampered:
        validate_rockbox_helper_build(helper, source, manifest)
    assert source_tampered.value.code == "helper_source_patch_mismatch"

    helper.write_bytes(helper.read_bytes() + b"tampered")
    with pytest.raises(ArtifactValidationError) as tampered:
        validate_rockbox_helper_build(helper, source, manifest)
    assert tampered.value.code == "helper_hash_mismatch"


def test_artifact_metadata_rejects_insecure_or_unbounded_values() -> None:
    payload = b"x" * 4096
    kwargs = dict(
        artifact_id="unsafe-artifact",
        filename="unsafe.bin",
        expected_size=len(payload),
        sha1=None,
        sha256=hashlib.sha256(payload).hexdigest(),
        kind="test",
        license_expression="test",
        source_revision="test",
        redistributable=False,
    )
    with pytest.raises(ArtifactMetadataError) as insecure:
        ArtifactSpec(url="http://fixtures.invalid/unsafe.bin", **kwargs)
    assert insecure.value.code == "insecure_url"

    with pytest.raises(ArtifactMetadataError) as traversal:
        ArtifactSpec(
            url="https://fixtures.invalid/unsafe.bin",
            **{**kwargs, "filename": "../unsafe.bin"},
        )
    assert traversal.value.code == "invalid_filename"


def test_local_validation_streams_and_fails_closed_on_size_or_hash(
    tmp_path: Path,
) -> None:
    payload = (b"safe-artifact-" * 400)[:5000]
    spec = make_spec(payload)
    path = tmp_path / spec.filename
    path.write_bytes(payload)
    progress = []

    receipt = validate_artifact_file(
        path, spec, progress=progress.append, chunk_size=4096
    )
    assert receipt.size == len(payload)
    assert receipt.sha1 == hashlib.sha1(payload).hexdigest()
    assert receipt.sha256 == hashlib.sha256(payload).hexdigest()
    assert progress[0].current_bytes == 0
    assert progress[-1].stage == "validating:verified"

    path.write_bytes(payload + b"x")
    with pytest.raises(ArtifactValidationError) as wrong_size:
        validate_artifact_file(path, spec)
    assert wrong_size.value.code == "size_mismatch"

    wrong_same_size = bytearray(payload)
    wrong_same_size[-1] ^= 1
    path.write_bytes(wrong_same_size)
    with pytest.raises(ArtifactValidationError) as wrong_hash:
        validate_artifact_file(path, spec)
    assert wrong_hash.value.code in {"sha1_mismatch", "sha256_mismatch"}


def test_download_requires_explicit_action_and_uses_no_network(tmp_path: Path) -> None:
    payload = b"n" * 4096
    spec = make_spec(payload)
    opener = FakeOpener(payload)

    with pytest.raises(ArtifactDownloadError) as blocked:
        ArtifactDownloader(opener).download(
            spec,
            tmp_path / spec.filename,
            explicit_user_action=False,
        )

    assert blocked.value.code == "explicit_action_required"
    assert opener.calls == []


def test_fake_stream_download_is_atomic_and_reports_progress(tmp_path: Path) -> None:
    # This is an injected in-memory stream, not a network download.
    payload = (b"verified-stream-" * 500)[:7000]
    spec = make_spec(payload)
    destination = tmp_path / spec.filename
    destination.write_bytes(b"old")
    opener = FakeOpener(payload, content_length=len(payload))
    progress = []

    receipt = ArtifactDownloader(opener).download(
        spec,
        destination,
        explicit_user_action=True,
        timeout=5,
        progress=progress.append,
        chunk_size=4096,
    )

    assert destination.read_bytes() == payload
    assert receipt.sha256 == spec.sha256
    assert opener.calls[0][1] == 5.0
    assert progress[-1].stage == "downloading:verified"
    assert list(tmp_path.glob("*.part")) == []


def test_hash_failure_or_cancellation_preserves_existing_destination(
    tmp_path: Path,
) -> None:
    payload = b"a" * 9000
    spec = make_spec(payload)
    destination = tmp_path / spec.filename
    destination.write_bytes(b"existing")

    corrupt = bytearray(payload)
    corrupt[-1] ^= 1
    with pytest.raises(ArtifactValidationError):
        ArtifactDownloader(FakeOpener(bytes(corrupt))).download(
            spec,
            destination,
            explicit_user_action=True,
            chunk_size=4096,
        )
    assert destination.read_bytes() == b"existing"

    cancellation_checks = 0

    def cancelled() -> bool:
        nonlocal cancellation_checks
        cancellation_checks += 1
        return cancellation_checks >= 3

    with pytest.raises(ArtifactCancelled):
        ArtifactDownloader(FakeOpener(payload)).download(
            spec,
            destination,
            explicit_user_action=True,
            cancelled=cancelled,
            chunk_size=4096,
        )
    assert destination.read_bytes() == b"existing"
    assert list(tmp_path.glob("*.part")) == []


def test_redirect_content_length_and_timeout_are_bounded(tmp_path: Path) -> None:
    payload = b"z" * 4096
    spec = make_spec(payload)
    destination = tmp_path / spec.filename

    with pytest.raises(ArtifactDownloadError) as redirect:
        ArtifactDownloader(
            FakeOpener(payload, final_url="http://fixtures.invalid/final.bin")
        ).download(spec, destination, explicit_user_action=True)
    assert redirect.value.code == "insecure_redirect"

    with pytest.raises(ArtifactValidationError) as wrong_length:
        ArtifactDownloader(
            FakeOpener(payload, content_length=len(payload) + 1)
        ).download(spec, destination, explicit_user_action=True)
    assert wrong_length.value.code == "size_mismatch"

    with pytest.raises(ArtifactDownloadError) as timeout:
        ArtifactDownloader(FakeOpener(payload)).download(
            spec,
            destination,
            explicit_user_action=True,
            timeout=121,
        )
    assert timeout.value.code == "invalid_timeout"
