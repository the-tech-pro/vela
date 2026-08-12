import hashlib
import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import build_desktop as build
from antra import __version__


def write_icns(path: Path, payload: bytes = b"approved-icon-fixture") -> Path:
    data = b"icns" + (8 + len(payload)).to_bytes(4, byteorder="big") + payload
    path.write_bytes(data)
    return path


class BuildModeAndMetadataTests(unittest.TestCase):
    def test_build_mode_defaults_to_development_and_accepts_production_alias(self):
        self.assertEqual(build.resolve_build_mode(None, {}), "development")
        self.assertEqual(
            build.resolve_build_mode(None, {"VELA_BUILD_MODE": "production"}),
            "release",
        )
        with self.assertRaisesRegex(SystemExit, "Unsupported build mode"):
            build.resolve_build_mode("nightly", {})

    def test_product_version_is_canonical_and_not_hardcoded_in_plist_template(self):
        self.assertEqual(build.read_product_version(), __version__)
        template = (build.MACOS_RESOURCES / "Info.plist").read_text(encoding="utf-8")
        self.assertIn("@VELA_VERSION@", template)
        self.assertNotIn(f"<string>{__version__}</string>", template)

    def test_release_metadata_requires_real_bundle_id_and_valid_icns(self):
        with self.assertRaisesRegex(SystemExit, "require VELA_BUNDLE_ID"):
            build.resolve_macos_metadata("release", {})
        with self.assertRaisesRegex(SystemExit, "placeholder/local"):
            build.resolve_macos_metadata(
                "release",
                {
                    "VELA_BUNDLE_ID": "com.example.vela",
                    "VELA_ICON_ICNS": "unused.icns",
                },
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            icon = write_icns(Path(temp_dir) / "Vela.icns")
            bundle_id, icon_path = build.resolve_macos_metadata(
                "release",
                {
                    "VELA_BUNDLE_ID": "com.acme.vela",
                    "VELA_ICON_ICNS": str(icon),
                },
            )
        self.assertEqual(bundle_id, "com.acme.vela")
        self.assertEqual(icon_path.name, "Vela.icns")

    def test_development_metadata_is_marked_and_may_omit_icon(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app = Path(temp_dir) / "Vela.app"
            (app / "Contents").mkdir(parents=True)
            bundle_id, icon_path = build.resolve_macos_metadata("development", {})
            build.install_macos_metadata(
                app,
                build_mode="development",
                version=__version__,
                bundle_id=bundle_id,
                icon_path=icon_path,
            )
            plist = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())

        self.assertEqual(plist["CFBundleIdentifier"], build.DEVELOPMENT_BUNDLE_ID)
        self.assertEqual(plist["CFBundleShortVersionString"], __version__)
        self.assertEqual(plist["CFBundleVersion"], __version__)
        self.assertEqual(plist["VelaBuildMode"], "development")
        self.assertNotIn("CFBundleIconFile", plist)

    def test_release_metadata_copies_validated_icon_and_injects_version(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            app = root / "Vela.app"
            (app / "Contents").mkdir(parents=True)
            icon = write_icns(root / "Approved.icns")
            build.install_macos_metadata(
                app,
                build_mode="release",
                version=__version__,
                bundle_id="com.acme.vela",
                icon_path=build.validate_icns(icon),
            )
            plist = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())

            self.assertEqual(plist["CFBundleIdentifier"], "com.acme.vela")
            self.assertEqual(plist["CFBundleShortVersionString"], __version__)
            self.assertEqual(plist["VelaBuildMode"], "release")
            self.assertEqual(plist["CFBundleIconFile"], "Vela.icns")
            self.assertEqual(
                (app / "Contents" / "Resources" / "Vela.icns").read_bytes(),
                icon.read_bytes(),
            )

    def test_icns_validator_rejects_extension_and_header_length_mismatch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            wrong_extension = root / "Vela.png"
            wrong_extension.write_bytes(b"icns\x00\x00\x00\x08")
            with self.assertRaisesRegex(SystemExit, "existing .icns"):
                build.validate_icns(wrong_extension)

            bad_length = root / "Vela.icns"
            bad_length.write_bytes(b"icns\x00\x00\x00\x09")
            with self.assertRaisesRegex(SystemExit, "Invalid .icns length"):
                build.validate_icns(bad_length)


class MediaToolValidationTests(unittest.TestCase):
    def make_tools(self, root: Path) -> tuple[Path, Path]:
        tools_dir = root / "tools"
        tools_dir.mkdir()
        lines = []
        for name in build.TOOL_NAMES:
            path = tools_dir / name
            path.write_bytes(f"native-{name}".encode())
            lines.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {name}")
        manifest = root / "SHA256SUMS"
        manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return tools_dir, manifest

    def test_release_macos_requires_checksum_manifest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tools_dir, _ = self.make_tools(Path(temp_dir))
            with self.assertRaisesRegex(SystemExit, "require VELA_TOOLS_CHECKSUMS"):
                build.validate_tools(
                    build_mode="release",
                    target_arch="arm64",
                    platform_name="darwin",
                    environ={"VELA_TOOLS_DIR": str(tools_dir)},
                )

    def test_release_macos_checksums_every_tool_and_validates_architecture(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tools_dir, manifest = self.make_tools(Path(temp_dir))
            with patch.object(build, "validate_macho_architecture") as validate_arch:
                result = build.validate_tools(
                    build_mode="release",
                    target_arch="arm64",
                    platform_name="darwin",
                    environ={
                        "VELA_TOOLS_DIR": str(tools_dir),
                        "VELA_TOOLS_CHECKSUMS": str(manifest),
                    },
                )

        self.assertEqual(result, tools_dir.resolve())
        self.assertEqual(validate_arch.call_count, len(build.TOOL_NAMES))
        self.assertEqual(
            {call.args[0].name for call in validate_arch.call_args_list},
            set(build.TOOL_NAMES),
        )
        self.assertTrue(all(call.args[1] == "arm64" for call in validate_arch.call_args_list))

    def test_checksum_mismatch_and_duplicate_entries_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            tools_dir, manifest = self.make_tools(root)
            manifest.write_text(f"{'0' * 64}  ffmpeg\n", encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "SHA-256 mismatch"):
                build.validate_tools(
                    build_mode="development",
                    target_arch="amd64",
                    platform_name="linux",
                    environ={
                        "VELA_TOOLS_DIR": str(tools_dir),
                        "VELA_TOOLS_CHECKSUMS": str(manifest),
                    },
                )

            manifest.write_text(
                f"{'0' * 64}  ffmpeg\n{'1' * 64}  ffmpeg\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SystemExit, "Duplicate checksum"):
                build.load_checksums(manifest)

    def test_manifest_rejects_non_sha256_and_path_entries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest = Path(temp_dir) / "SHA256SUMS"
            manifest.write_text("abcd  ffmpeg\n", encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "Invalid SHA-256"):
                build.load_checksums(manifest)

            manifest.write_text(f"{'a' * 64}  bin/ffmpeg\n", encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "plain basenames"):
                build.load_checksums(manifest)


class MachOValidationTests(unittest.TestCase):
    def test_macho_architecture_requires_target_slice_and_can_require_thin_output(self):
        binary = Path("native-binary")
        with patch.object(build, "macho_architectures", return_value={"arm64"}):
            build.validate_macho_architecture(binary, "arm64")
        with patch.object(
            build,
            "macho_architectures",
            return_value={"arm64", "amd64"},
        ):
            build.validate_macho_architecture(binary, "arm64")
        with (
            patch.object(
                build,
                "macho_architectures",
                return_value={"arm64", "amd64"},
            ),
            self.assertRaisesRegex(SystemExit, "expected only arm64"),
        ):
            build.validate_macho_architecture(binary, "arm64", require_thin=True)
        with (
            patch.object(build, "macho_architectures", return_value={"amd64"}),
            self.assertRaisesRegex(SystemExit, "expected target slice arm64"),
        ):
            build.validate_macho_architecture(binary, "arm64")

    def test_assembled_bundle_checks_every_macho_and_required_helper_location(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app = Path(temp_dir) / "Vela.app"
            main = app / "Contents" / "MacOS" / "Vela"
            helper = app / "Contents" / "Helpers" / "VelaBackend" / "VelaBackend"
            framework = app / "Contents" / "Frameworks" / "libfixture.dylib"
            resource = app / "Contents" / "Resources" / "fixture.txt"
            for path in (main, helper, framework, resource):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"fixture")

            with (
                patch.object(
                    build,
                    "is_macho_file",
                    side_effect=lambda path: path != resource,
                ),
                patch.object(build, "validate_macho_architecture") as validate_arch,
            ):
                found = build.validate_macos_bundle_architectures(app, "amd64")

        self.assertEqual(set(found), {main, helper, framework})
        self.assertEqual(validate_arch.call_count, 3)
        self.assertTrue(all(call.args[1] == "amd64" for call in validate_arch.call_args_list))
        required_calls = [
            call
            for call in validate_arch.call_args_list
            if call.args[0] in {main, helper}
        ]
        self.assertTrue(all(call.kwargs.get("require_thin") for call in required_calls))

    def test_assembled_bundle_rejects_missing_backend_helper(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app = Path(temp_dir) / "Vela.app"
            main = app / "Contents" / "MacOS" / "Vela"
            main.parent.mkdir(parents=True)
            main.write_bytes(b"fixture")
            with self.assertRaisesRegex(SystemExit, "Required macOS executable"):
                build.validate_macos_bundle_architectures(app, "arm64")


if __name__ == "__main__":
    unittest.main()
