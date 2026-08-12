from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
UTILITY_PATH = REPOSITORY_ROOT / "packaging" / "release" / "release_metadata.py"
SPEC = importlib.util.spec_from_file_location("vela_release_metadata", UTILITY_PATH)
assert SPEC is not None and SPEC.loader is not None
release_metadata = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_metadata)


class ReleaseSpdxOwnerEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.repo_root = self.root / "repository"
        (self.repo_root / "antra").mkdir(parents=True)
        (self.repo_root / "antra" / "__init__.py").write_text(
            '__version__ = "9.8.7"\n',
            encoding="utf-8",
        )
        self.release_root = self.root / "staging"
        (self.release_root / "tools").mkdir(parents=True)
        self.artifact = self.release_root / "Vela.exe"
        self.artifact.write_bytes(b"vela executable fixture")
        self.media_file = self.release_root / "tools" / "ffmpeg.exe"
        self.media_file.write_bytes(b"pinned ffmpeg fixture")
        self.media_hash = hashlib.sha256(self.media_file.read_bytes()).hexdigest()
        self.ffprobe_file = self.release_root / "tools" / "ffprobe.exe"
        self.ffprobe_file.write_bytes(b"pinned ffprobe fixture")
        self.ffprobe_hash = hashlib.sha256(self.ffprobe_file.read_bytes()).hexdigest()
        self.fpcalc_file = self.release_root / "tools" / "fpcalc.exe"
        self.fpcalc_file.write_bytes(b"pinned fpcalc fixture")
        self.fpcalc_hash = hashlib.sha256(self.fpcalc_file.read_bytes()).hexdigest()
        self.owner_metadata = {
            "media_tools": [
                {
                    "name": "FFmpeg",
                    "version": "7.1.1",
                    "upstream_url": "https://ffmpeg.org/",
                    "source_url": "https://ffmpeg.org/releases/ffmpeg-7.1.1.tar.xz",
                    "declared_license": "GPL-3.0-or-later",
                    "build_configuration": "--disable-debug --enable-gpl",
                    "source_offer": "https://downloads.example.org/vela/sources/ffmpeg-7.1.1.tar.xz",
                    "files": [
                        {
                            "path": "tools/ffmpeg.exe",
                            "sha256": self.media_hash,
                        },
                        {
                            "path": "tools/ffprobe.exe",
                            "sha256": self.ffprobe_hash,
                        },
                    ],
                },
                {
                    "name": "Chromaprint",
                    "version": "1.5.1",
                    "upstream_url": "https://acoustid.org/chromaprint",
                    "source_url": "https://github.com/acoustid/chromaprint/archive/refs/tags/v1.5.1.tar.gz",
                    "declared_license": "LGPL-2.1-or-later",
                    "build_configuration": "owner-reviewed native release build",
                    "source_offer": "https://downloads.example.org/vela/sources/chromaprint-1.5.1.tar.gz",
                    "files": [
                        {
                            "path": "tools/fpcalc.exe",
                            "sha256": self.fpcalc_hash,
                        },
                    ],
                }
            ]
        }

    def _generate(self, artifact: Path | None = None) -> dict:
        return release_metadata.generate_spdx_document(
            repo_root=self.repo_root,
            artifact=artifact or self.artifact,
            owner_metadata=self.owner_metadata,
            target_platform="windows",
            target_architecture="amd64",
            media_root=self.release_root,
            python_distributions=[
                {"name": "Example-Python-Package", "version": "1.2.3"}
            ],
            go_modules=[
                {"path": "example.org/go/module", "version": "v1.4.0"},
                {"path": "example.org/vela", "main": True},
            ],
            npm_packages=[
                {
                    "name": "@example/ui",
                    "version": "4.5.6",
                    "path": "node_modules/@example/ui",
                    "dev": False,
                    "license": "MIT",
                }
            ],
            git_commit="a" * 40,
            created="2026-08-12T20:00:00Z",
            namespace="https://spdx.example.org/vela/test-document",
        )

    def test_reads_canonical_version_without_importing_application(self) -> None:
        self.assertEqual(
            release_metadata.read_canonical_version(self.repo_root),
            "9.8.7",
        )

    def test_example_owner_metadata_is_intentionally_rejected(self) -> None:
        example = (
            REPOSITORY_ROOT
            / "packaging"
            / "release"
            / "owner-metadata.example.json"
        )
        with self.assertRaisesRegex(release_metadata.MetadataError, "non-placeholder"):
            release_metadata.load_owner_metadata(example)

        stderr = StringIO()
        with redirect_stderr(stderr):
            exit_code = release_metadata.main(
                ["validate-owner", "--metadata", str(example)]
            )
        self.assertEqual(exit_code, 2)
        self.assertIn("error:", stderr.getvalue())

    def test_owner_metadata_requires_exact_matching_media_hashes(self) -> None:
        bad_metadata = json.loads(json.dumps(self.owner_metadata))
        bad_metadata["media_tools"][0]["files"][0]["sha256"] = ("b" * 63) + "c"
        normalized = release_metadata.validate_owner_metadata(bad_metadata)
        with self.assertRaisesRegex(release_metadata.MetadataError, "mismatch"):
            release_metadata.verify_media_tool_files(
                normalized,
                self.release_root,
            )

        bad_license = json.loads(json.dumps(self.owner_metadata))
        bad_license["media_tools"][0]["declared_license"] = "GPL-3.0-or-later MIT"
        with self.assertRaisesRegex(release_metadata.MetadataError, "SPDX license expression"):
            release_metadata.validate_owner_metadata(bad_license)

        missing_tool = json.loads(json.dumps(self.owner_metadata))
        missing_tool["media_tools"] = missing_tool["media_tools"][:1]
        with self.assertRaisesRegex(release_metadata.MetadataError, "fpcalc"):
            release_metadata.validate_owner_metadata(missing_tool)

        metadata_path = self.root / "owner-metadata.json"
        metadata_path.write_text(json.dumps(self.owner_metadata), encoding="utf-8")
        stdout = StringIO()
        with redirect_stdout(stdout):
            exit_code = release_metadata.main(
                [
                    "validate-owner",
                    "--metadata",
                    str(metadata_path),
                    "--media-root",
                    str(self.release_root),
                ]
            )
        self.assertEqual(exit_code, 0)
        self.assertIn("verified hashes", stdout.getvalue())

    def test_generates_deterministic_spdx_23_release_document(self) -> None:
        document = self._generate()
        repeated = self._generate()
        self.assertEqual(document, repeated)
        self.assertEqual(document["spdxVersion"], "SPDX-2.3")
        self.assertEqual(document["dataLicense"], "CC0-1.0")
        self.assertEqual(document["SPDXID"], "SPDXRef-DOCUMENT")
        self.assertEqual(
            document["documentNamespace"],
            "https://spdx.example.org/vela/test-document",
        )
        self.assertEqual(document["creationInfo"]["created"], "2026-08-12T20:00:00Z")

        artifact_package = document["packages"][0]
        self.assertEqual(artifact_package["name"], "Vela.exe")
        self.assertEqual(artifact_package["versionInfo"], "9.8.7")
        self.assertEqual(
            artifact_package["checksums"][0]["checksumValue"],
            hashlib.sha256(self.artifact.read_bytes()).hexdigest(),
        )
        self.assertIn("Target platform: windows", artifact_package["comment"])
        self.assertIn("Target architecture: amd64", artifact_package["comment"])
        self.assertIn("Git commit: " + ("a" * 40), artifact_package["comment"])

        packages_by_name = {
            package["name"]: package for package in document["packages"]
        }
        self.assertEqual(packages_by_name["FFmpeg"]["versionInfo"], "7.1.1")
        self.assertEqual(packages_by_name["FFmpeg"]["licenseDeclared"], "GPL-3.0-or-later")
        self.assertEqual(packages_by_name["FFmpeg"]["homepage"], "https://ffmpeg.org/")
        self.assertIn(
            "--disable-debug --enable-gpl",
            packages_by_name["FFmpeg"]["sourceInfo"],
        )
        self.assertIn(
            "ffmpeg-7.1.1.tar.xz",
            packages_by_name["FFmpeg"]["sourceInfo"],
        )
        self.assertEqual(
            packages_by_name["Example-Python-Package"]["versionInfo"],
            "1.2.3",
        )
        self.assertEqual(
            packages_by_name["example.org/go/module"]["versionInfo"],
            "v1.4.0",
        )
        self.assertEqual(packages_by_name["@example/ui"]["versionInfo"], "4.5.6")
        self.assertEqual(
            packages_by_name["@example/ui"]["externalRefs"][0]["referenceLocator"],
            "pkg:npm/%40example/ui@4.5.6",
        )

        self.assertEqual(len(document["files"]), 3)
        self.assertEqual(document["files"][0]["fileName"], "./tools/ffmpeg.exe")
        self.assertEqual(
            document["files"][0]["checksums"][0]["checksumValue"],
            self.media_hash,
        )

        element_ids = {"SPDXRef-DOCUMENT"}
        element_ids.update(package["SPDXID"] for package in document["packages"])
        element_ids.update(file["SPDXID"] for file in document["files"])
        self.assertEqual(
            len(element_ids),
            1 + len(document["packages"]) + len(document["files"]),
        )
        for relationship in document["relationships"]:
            self.assertIn(relationship["spdxElementId"], element_ids)
            self.assertIn(relationship["relatedSpdxElement"], element_ids)

    def test_directory_artifact_uses_stable_tree_sha256(self) -> None:
        app_directory = self.release_root / "Vela.app"
        (app_directory / "Contents").mkdir(parents=True)
        (app_directory / "Contents" / "Vela").write_bytes(b"app fixture")
        first = release_metadata.sha256_path(app_directory)
        second = release_metadata.sha256_path(app_directory)
        self.assertEqual(first, second)
        self.assertRegex(first, r"^[0-9a-f]{64}$")

        document = self._generate(artifact=app_directory)
        self.assertEqual(
            document["packages"][0]["checksums"][0]["checksumValue"],
            first,
        )
        self.assertIn(
            "Artifact hash input: canonical directory tree",
            document["packages"][0]["comment"],
        )

    def test_augments_package_scan_with_final_artifact_and_owner_evidence(self) -> None:
        base_package_id = "SPDXRef-Package-scanned-app"
        base_document = {
            "spdxVersion": "SPDX-2.3",
            "dataLicense": "CC0-1.0",
            "SPDXID": "SPDXRef-DOCUMENT",
            "name": "package scan",
            "documentNamespace": "https://spdx.example.org/vela/package-scan",
            "creationInfo": {
                "created": "2026-08-12T20:00:00Z",
                "creators": ["Tool: package scanner"],
            },
            "packages": [
                {
                    "name": "Vela.app",
                    "SPDXID": base_package_id,
                    "downloadLocation": "NOASSERTION",
                    "filesAnalyzed": False,
                    "licenseConcluded": "NOASSERTION",
                    "licenseDeclared": "NOASSERTION",
                    "copyrightText": "NOASSERTION",
                }
            ],
            "files": [],
            "relationships": [
                {
                    "spdxElementId": "SPDXRef-DOCUMENT",
                    "relationshipType": "DESCRIBES",
                    "relatedSpdxElement": base_package_id,
                }
            ],
        }

        document = release_metadata.augment_spdx_document(
            base_document=base_document,
            repo_root=self.repo_root,
            artifact=self.artifact,
            owner_metadata=self.owner_metadata,
            target_platform="windows",
            target_architecture="amd64",
            media_root=self.release_root,
            git_commit="b" * 40,
        )

        packages_by_name = {
            package["name"]: package for package in document["packages"]
        }
        self.assertIn("Vela.app", packages_by_name)
        self.assertIn("Vela.exe", packages_by_name)
        self.assertIn("FFmpeg", packages_by_name)
        self.assertEqual(
            packages_by_name["Vela.exe"]["checksums"][0]["checksumValue"],
            hashlib.sha256(self.artifact.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            document["creationInfo"]["creators"],
            ["Tool: package scanner", f"Tool: {release_metadata.TOOL_NAME}"],
        )
        self.assertIn(
            {
                "spdxElementId": "SPDXRef-DOCUMENT",
                "relationshipType": "DESCRIBES",
                "relatedSpdxElement": packages_by_name["Vela.exe"]["SPDXID"],
            },
            document["relationships"],
        )
        self.assertEqual(len(document["packages"]), 4)

    def test_filename_audit_rejects_credentials_without_scanning_contents(self) -> None:
        safe_directory = self.root / "safe-app"
        safe_directory.mkdir()
        (safe_directory / "ordinary_source.py").write_text(
            "embedded_words = '.p12 cookies.json access_token'\n",
            encoding="utf-8",
        )
        self.assertEqual(
            release_metadata.audit_package_content(safe_directory),
            [],
        )

        (safe_directory / "device.wvd").write_bytes(b"fixture")
        (safe_directory / "signing.p12").write_bytes(b"fixture")
        (safe_directory / "login-cookies.json").write_text("{}", encoding="utf-8")
        (safe_directory / "oauth_token.txt").write_text("fixture", encoding="utf-8")
        (safe_directory / "build.keychain-db").write_bytes(b"fixture")
        findings = release_metadata.audit_package_content(safe_directory)
        self.assertEqual(
            [finding["path"] for finding in findings],
            [
                "build.keychain-db",
                "device.wvd",
                "login-cookies.json",
                "oauth_token.txt",
                "signing.p12",
            ],
        )

        single_file_findings = release_metadata.audit_package_content(
            safe_directory / "signing.p12"
        )
        self.assertEqual(single_file_findings[0]["path"], "signing.p12")

        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = release_metadata.main(
                ["audit", "--json", str(safe_directory)]
            )
        self.assertEqual(exit_code, 1)
        self.assertEqual(len(json.loads(stdout.getvalue())["findings"]), 5)
        self.assertEqual(stderr.getvalue(), "")

    def test_parses_go_module_stream_and_npm_lock_without_network(self) -> None:
        go_modules = release_metadata.parse_go_module_json(
            '{"Path":"example.org/main","Main":true}\n'
            '{"Path":"example.org/library","Version":"v1.2.0",'
            '"Replace":{"Path":"../library"}}\n'
        )
        self.assertEqual(go_modules[0]["path"], "example.org/library")
        self.assertEqual(go_modules[0]["replace"]["path"], "../library")
        self.assertTrue(go_modules[1]["main"])

        package_lock = self.root / "package-lock.json"
        package_lock.write_text(
            json.dumps(
                {
                    "lockfileVersion": 3,
                    "packages": {
                        "": {"name": "fixture", "version": "1.0.0"},
                        "node_modules/plain": {
                            "version": "2.0.0",
                            "license": "ISC",
                        },
                        "node_modules/@scope/pkg": {
                            "version": "3.0.0",
                            "dev": True,
                            "license": "MIT",
                        },
                    },
                }
            ),
            encoding="utf-8",
        )
        npm_packages = release_metadata.collect_npm_packages(package_lock)
        self.assertEqual(
            [(package["name"], package["version"]) for package in npm_packages],
            [("@scope/pkg", "3.0.0"), ("plain", "2.0.0")],
        )
        self.assertTrue(npm_packages[0]["dev"])


if __name__ == "__main__":
    unittest.main()
