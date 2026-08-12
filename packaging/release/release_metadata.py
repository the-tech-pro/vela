#!/usr/bin/env python3
"""Generate Vela release SPDX metadata and audit package file names.

This module intentionally uses only the Python standard library.  It does not
inspect file contents for secrets; the release audit is strictly name based.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import quote, urlsplit


SPDX_VERSION = "SPDX-2.3"
TOOL_NAME = "Vela release metadata utility"
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
SPDX_ID_RE = re.compile(r"[^A-Za-z0-9.-]+")
LICENSE_TOKEN_RE = re.compile(
    r"\(|\)|\bAND\b|\bOR\b|\bWITH\b|[A-Za-z0-9][A-Za-z0-9.+:-]*"
)
PLACEHOLDER_MARKERS = (
    "replace_me",
    "replace-me",
    "replace with",
    "placeholder",
    "change_me",
    "changeme",
    "todo",
    "tbd",
    "example.invalid",
    "<",
    ">",
)
SENSITIVE_SUFFIXES = {
    ".wvd": "Widevine device credential",
    ".p12": "PKCS#12 signing material",
    ".pfx": "PKCS#12 signing material",
    ".jks": "Java signing key store",
    ".keystore": "signing key store",
    ".key": "private/signing key",
}
CREDENTIAL_DATA_SUFFIXES = {
    "",
    ".conf",
    ".cfg",
    ".dat",
    ".db",
    ".ini",
    ".json",
    ".sqlite",
    ".sqlite3",
    ".txt",
    ".yaml",
    ".yml",
}
CREDENTIAL_WORDS = {
    "auth_token",
    "authentication_token",
    "client_secret",
    "cookie",
    "cookies",
    "credential",
    "credentials",
    "oauth_token",
    "refresh_token",
    "secret_token",
    "token",
    "tokens",
}


class MetadataError(ValueError):
    """Raised when release metadata cannot be trusted."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_path(path: Path) -> str:
    """Hash a regular file or a directory using a canonical tree encoding."""

    path = Path(path)
    if path.is_file():
        return _sha256_file(path)
    if not path.is_dir():
        raise MetadataError(f"Artifact does not exist or is unsupported: {path}")

    digest = hashlib.sha256()
    entries = sorted(path.rglob("*"), key=lambda item: item.relative_to(path).as_posix())
    for entry in entries:
        relative = entry.relative_to(path).as_posix().encode("utf-8")
        if entry.is_symlink():
            target = os.readlink(entry).replace("\\", "/").encode("utf-8")
            digest.update(b"L\0" + relative + b"\0" + target + b"\0")
        elif entry.is_dir():
            digest.update(b"D\0" + relative + b"\0")
        elif entry.is_file():
            digest.update(
                b"F\0"
                + relative
                + b"\0"
                + str(entry.stat().st_size).encode("ascii")
                + b"\0"
                + _sha256_file(entry).encode("ascii")
                + b"\0"
            )
    return digest.hexdigest()


def read_canonical_version(repo_root: Path) -> str:
    """Read ``__version__`` without importing application code."""

    version_file = Path(repo_root) / "antra" / "__init__.py"
    try:
        module = ast.parse(version_file.read_text(encoding="utf-8"), filename=str(version_file))
    except (OSError, SyntaxError) as exc:
        raise MetadataError(f"Cannot read canonical version from {version_file}: {exc}") from exc

    for statement in module.body:
        if (
            isinstance(statement, (ast.Assign, ast.AnnAssign))
            and isinstance(statement.value, ast.Constant)
            and isinstance(statement.value.value, str)
        ):
            targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
            if any(isinstance(target, ast.Name) and target.id == "__version__" for target in targets):
                version = statement.value.value.strip()
                if version:
                    return version
    raise MetadataError(f"No string __version__ assignment found in {version_file}")


def _has_placeholder(value: str) -> bool:
    lowered = value.strip().lower()
    return not lowered or any(marker in lowered for marker in PLACEHOLDER_MARKERS)


def _required_text(item: Mapping[str, Any], field: str, context: str) -> str:
    value = item.get(field)
    if not isinstance(value, str) or _has_placeholder(value):
        raise MetadataError(f"{context}.{field} must be a non-placeholder string")
    return value.strip()


def _required_url(item: Mapping[str, Any], field: str, context: str) -> str:
    value = _required_text(item, field, context)
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise MetadataError(f"{context}.{field} must be an absolute HTTP(S) URL")
    return value


def _normalized_relative_path(value: str, context: str) -> str:
    if _has_placeholder(value):
        raise MetadataError(f"{context} must be a non-placeholder relative path")
    windows_path = PureWindowsPath(value)
    posix_path = PurePosixPath(value.replace("\\", "/"))
    if windows_path.is_absolute() or posix_path.is_absolute() or ".." in posix_path.parts:
        raise MetadataError(f"{context} must stay within the media root")
    normalized = posix_path.as_posix()
    if normalized in {"", "."}:
        raise MetadataError(f"{context} must name a file")
    return normalized


def _is_spdx_license_expression(value: str) -> bool:
    compact = re.sub(r"\s+", "", value)
    tokens = LICENSE_TOKEN_RE.findall(value)
    if not tokens or "".join(tokens) != compact:
        return False

    offset = 0

    def parse_primary() -> bool:
        nonlocal offset
        if offset >= len(tokens):
            return False
        if tokens[offset] == "(":
            offset += 1
            if not parse_or() or offset >= len(tokens) or tokens[offset] != ")":
                return False
            offset += 1
            return True
        if tokens[offset] in {"AND", "OR", "WITH", ")", "NONE", "NOASSERTION"}:
            return False
        offset += 1
        return True

    def parse_with() -> bool:
        nonlocal offset
        if not parse_primary():
            return False
        if offset < len(tokens) and tokens[offset] == "WITH":
            offset += 1
            if offset >= len(tokens) or tokens[offset] in {
                "AND",
                "OR",
                "WITH",
                "(",
                ")",
                "NONE",
                "NOASSERTION",
            }:
                return False
            offset += 1
        return True

    def parse_and() -> bool:
        nonlocal offset
        if not parse_with():
            return False
        while offset < len(tokens) and tokens[offset] == "AND":
            offset += 1
            if not parse_with():
                return False
        return True

    def parse_or() -> bool:
        nonlocal offset
        if not parse_and():
            return False
        while offset < len(tokens) and tokens[offset] == "OR":
            offset += 1
            if not parse_and():
                return False
        return True

    return parse_or() and offset == len(tokens)


def validate_owner_metadata(data: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize owner-supplied bundled media-tool metadata."""

    if not isinstance(data, Mapping):
        raise MetadataError("Owner metadata must be a JSON object")
    tools = data.get("media_tools")
    if not isinstance(tools, list) or not tools:
        raise MetadataError("Owner metadata must contain a non-empty media_tools array")

    normalized_tools: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    seen_paths: set[str] = set()
    for index, raw_tool in enumerate(tools):
        context = f"media_tools[{index}]"
        if not isinstance(raw_tool, Mapping):
            raise MetadataError(f"{context} must be an object")
        tool = {
            "name": _required_text(raw_tool, "name", context),
            "version": _required_text(raw_tool, "version", context),
            "upstream_url": _required_url(raw_tool, "upstream_url", context),
            "source_url": _required_url(raw_tool, "source_url", context),
            "declared_license": _required_text(raw_tool, "declared_license", context),
            "build_configuration": _required_text(raw_tool, "build_configuration", context),
            "source_offer": _required_text(raw_tool, "source_offer", context),
        }
        if not _is_spdx_license_expression(tool["declared_license"]):
            raise MetadataError(f"{context}.declared_license is not an SPDX license expression")

        name_key = tool["name"].casefold()
        if name_key in seen_names:
            raise MetadataError(f"Duplicate media tool name: {tool['name']}")
        seen_names.add(name_key)

        raw_files = raw_tool.get("files")
        if not isinstance(raw_files, list) or not raw_files:
            raise MetadataError(f"{context}.files must be a non-empty array")
        files: list[dict[str, str]] = []
        for file_index, raw_file in enumerate(raw_files):
            file_context = f"{context}.files[{file_index}]"
            if not isinstance(raw_file, Mapping):
                raise MetadataError(f"{file_context} must be an object")
            raw_path = _required_text(raw_file, "path", file_context)
            relative_path = _normalized_relative_path(raw_path, f"{file_context}.path")
            checksum = _required_text(raw_file, "sha256", file_context).lower()
            if not SHA256_RE.fullmatch(checksum) or len(set(checksum)) == 1:
                raise MetadataError(f"{file_context}.sha256 must be an exact, non-placeholder SHA-256")
            path_key = relative_path.casefold()
            if path_key in seen_paths:
                raise MetadataError(f"Duplicate bundled media-tool path: {relative_path}")
            seen_paths.add(path_key)
            files.append({"path": relative_path, "sha256": checksum})
        tool["files"] = sorted(files, key=lambda entry: entry["path"].casefold())
        normalized_tools.append(tool)

    declared_executables = {
        PurePosixPath(media_file["path"]).name.casefold().removesuffix(".exe")
        for tool in normalized_tools
        for media_file in tool["files"]
    }
    missing_executables = sorted(
        {"ffmpeg", "ffprobe", "fpcalc"} - declared_executables
    )
    if missing_executables:
        raise MetadataError(
            "Owner metadata is missing required media executables: "
            + ", ".join(missing_executables)
        )

    return {
        "media_tools": sorted(
            normalized_tools,
            key=lambda entry: (entry["name"].casefold(), entry["version"]),
        )
    }


def load_owner_metadata(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MetadataError(f"Cannot read owner metadata {path}: {exc}") from exc
    return validate_owner_metadata(data)


def verify_media_tool_files(owner_metadata: Mapping[str, Any], media_root: Path) -> None:
    """Verify every owner-declared bundled file against its exact SHA-256."""

    root = Path(media_root).resolve()
    if not root.is_dir():
        raise MetadataError(f"Media root must be a directory: {media_root}")
    for tool in owner_metadata["media_tools"]:
        for media_file in tool["files"]:
            candidate = (root / Path(media_file["path"])).resolve()
            try:
                candidate.relative_to(root)
            except ValueError as exc:
                raise MetadataError(f"Bundled media-tool path escapes media root: {media_file['path']}") from exc
            if not candidate.is_file():
                raise MetadataError(f"Bundled media-tool file is missing: {media_file['path']}")
            actual = _sha256_file(candidate)
            if actual != media_file["sha256"]:
                raise MetadataError(
                    f"Bundled media-tool SHA-256 mismatch for {media_file['path']}: "
                    f"expected {media_file['sha256']}, got {actual}"
                )


def _sensitive_filename_reason(name: str) -> str | None:
    lowered = name.casefold()
    suffix = Path(lowered).suffix
    if suffix in SENSITIVE_SUFFIXES:
        return SENSITIVE_SUFFIXES[suffix]
    if "keychain" in lowered:
        return "keychain/signing material"
    if lowered.endswith((".pem", ".der")) and any(
        word in lowered for word in ("private", "signing", "secret", "key")
    ):
        return "private/signing key"

    stem = Path(lowered).stem
    normalized_stem = re.sub(r"[^a-z0-9]+", "_", stem).strip("_")
    credential_match = (
        normalized_stem in CREDENTIAL_WORDS
        or normalized_stem.startswith(("cookies_", "credentials_", "token_"))
        or normalized_stem.endswith(("_cookies", "_credentials", "_token"))
    )
    if credential_match and suffix in CREDENTIAL_DATA_SUFFIXES:
        return "cookie/token credential file"
    return None


def audit_package_content(path: Path) -> list[dict[str, str]]:
    """Return deterministic filename-only findings for a file or app directory."""

    path = Path(path)
    if not path.exists() and not path.is_symlink():
        raise MetadataError(f"Audit path does not exist: {path}")

    candidates: list[tuple[str, Path]]
    if path.is_dir() and not path.is_symlink():
        candidates = [
            (entry.relative_to(path).as_posix(), entry)
            for entry in path.rglob("*")
            if entry.is_file() or entry.is_symlink()
        ]
    else:
        candidates = [(path.name, path)]

    findings: list[dict[str, str]] = []
    for relative, candidate in sorted(candidates, key=lambda item: item[0].casefold()):
        reason = _sensitive_filename_reason(candidate.name)
        if reason:
            findings.append({"path": relative, "reason": reason})
    return findings


def collect_python_distributions() -> list[dict[str, str]]:
    """Collect installed Python distribution names and exact versions."""

    found: set[tuple[str, str]] = set()
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata.get("Name")
        version = distribution.version
        if name and version:
            found.add((str(name).strip(), str(version).strip()))
    return [
        {"name": name, "version": version}
        for name, version in sorted(found, key=lambda item: (item[0].casefold(), item[1]))
    ]


def parse_go_module_json(text: str) -> list[dict[str, Any]]:
    """Parse the concatenated JSON objects emitted by ``go list -m -json all``."""

    decoder = json.JSONDecoder()
    offset = 0
    modules: list[dict[str, Any]] = []
    while offset < len(text):
        while offset < len(text) and text[offset].isspace():
            offset += 1
        if offset >= len(text):
            break
        try:
            value, offset = decoder.raw_decode(text, offset)
        except json.JSONDecodeError as exc:
            raise MetadataError(f"Invalid go list JSON at character {exc.pos}") from exc
        values = value if isinstance(value, list) else [value]
        for raw in values:
            if not isinstance(raw, Mapping) or not isinstance(raw.get("Path"), str):
                continue
            module: dict[str, Any] = {"path": raw["Path"]}
            if isinstance(raw.get("Version"), str) and raw["Version"]:
                module["version"] = raw["Version"]
            if raw.get("Main") is True:
                module["main"] = True
            replacement = raw.get("Replace")
            if isinstance(replacement, Mapping) and isinstance(replacement.get("Path"), str):
                module["replace"] = {"path": replacement["Path"]}
                if isinstance(replacement.get("Version"), str) and replacement["Version"]:
                    module["replace"]["version"] = replacement["Version"]
            modules.append(module)
    return sorted(
        modules,
        key=lambda module: (
            module["path"].casefold(),
            str(module.get("version", "")),
        ),
    )


def collect_go_modules(module_dir: Path, timeout_seconds: int = 30) -> list[dict[str, Any]]:
    """Collect Go modules without permitting network dependency resolution."""

    module_dir = Path(module_dir)
    if not (module_dir / "go.mod").is_file():
        return []
    environment = os.environ.copy()
    environment["GOPROXY"] = "off"
    environment["GOSUMDB"] = "off"
    try:
        result = subprocess.run(
            ["go", "list", "-m", "-json", "all"],
            cwd=module_dir,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    return parse_go_module_json(result.stdout)


def _npm_name_from_lock_path(lock_path: str) -> str:
    normalized = lock_path.replace("\\", "/").rstrip("/")
    if "node_modules/" in normalized:
        return normalized.rsplit("node_modules/", 1)[1]
    return normalized.rsplit("/", 1)[-1]


def _walk_v1_npm_dependencies(
    dependencies: Mapping[str, Any],
    prefix: str = "node_modules",
) -> Iterable[dict[str, Any]]:
    for name in sorted(dependencies, key=str.casefold):
        details = dependencies[name]
        if not isinstance(details, Mapping):
            continue
        location = f"{prefix}/{name}"
        yield {
            "name": name,
            "version": str(details.get("version", "")),
            "path": location,
            "dev": details.get("dev") is True,
            "license": details.get("license"),
        }
        nested = details.get("dependencies")
        if isinstance(nested, Mapping):
            yield from _walk_v1_npm_dependencies(nested, f"{location}/node_modules")


def collect_npm_packages(package_lock: Path) -> list[dict[str, Any]]:
    """Collect package instances from npm package-lock v1, v2, or v3 JSON."""

    package_lock = Path(package_lock)
    if not package_lock.is_file():
        return []
    try:
        data = json.loads(package_lock.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MetadataError(f"Cannot read npm package lock {package_lock}: {exc}") from exc

    packages: list[dict[str, Any]] = []
    raw_packages = data.get("packages") if isinstance(data, Mapping) else None
    if isinstance(raw_packages, Mapping):
        for lock_path in sorted(raw_packages, key=str.casefold):
            if not lock_path:
                continue
            details = raw_packages[lock_path]
            if not isinstance(details, Mapping):
                continue
            name = details.get("name") or _npm_name_from_lock_path(lock_path)
            version = details.get("version")
            if not isinstance(name, str) or not isinstance(version, str) or not name or not version:
                continue
            packages.append(
                {
                    "name": name,
                    "version": version,
                    "path": lock_path.replace("\\", "/"),
                    "dev": details.get("dev") is True,
                    "license": details.get("license"),
                }
            )
    elif isinstance(data, Mapping) and isinstance(data.get("dependencies"), Mapping):
        packages.extend(_walk_v1_npm_dependencies(data["dependencies"]))

    return sorted(
        packages,
        key=lambda package: (
            package["name"].casefold(),
            package["version"],
            package["path"].casefold(),
        ),
    )


def get_git_commit(repo_root: Path) -> str | None:
    """Return the checked-out commit without contacting a remote."""

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    commit = result.stdout.strip().lower()
    if result.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit):
        return commit
    return None


def _spdx_id(kind: str, name: str, identity: str) -> str:
    safe_name = SPDX_ID_RE.sub("-", name).strip(".-") or "item"
    suffix = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    return f"SPDXRef-{kind}-{safe_name[:48]}-{suffix}"


def _valid_declared_license(value: Any) -> str:
    if isinstance(value, str) and _is_spdx_license_expression(value):
        return value
    return "NOASSERTION"


def _package(
    *,
    name: str,
    spdx_id: str,
    version: str | None,
    download_location: str = "NOASSERTION",
    license_declared: str = "NOASSERTION",
) -> dict[str, Any]:
    package: dict[str, Any] = {
        "name": name,
        "SPDXID": spdx_id,
        "downloadLocation": download_location,
        "filesAnalyzed": False,
        "licenseConcluded": "NOASSERTION",
        "licenseDeclared": license_declared,
        "copyrightText": "NOASSERTION",
    }
    if version:
        package["versionInfo"] = version
    return package


def _purl_external_ref(locator: str) -> list[dict[str, str]]:
    return [
        {
            "referenceCategory": "PACKAGE-MANAGER",
            "referenceType": "purl",
            "referenceLocator": locator,
        }
    ]


def _dependency_relationship(artifact_id: str, dependency_id: str) -> dict[str, str]:
    return {
        "spdxElementId": artifact_id,
        "relationshipType": "DEPENDS_ON",
        "relatedSpdxElement": dependency_id,
    }


def _validate_target(value: str, label: str) -> str:
    value = value.strip()
    if _has_placeholder(value) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", value):
        raise MetadataError(f"Target {label} must be a non-placeholder identifier")
    return value


def generate_spdx_document(
    *,
    repo_root: Path,
    artifact: Path,
    owner_metadata: Mapping[str, Any],
    target_platform: str,
    target_architecture: str,
    media_root: Path | None = None,
    package_lock: Path | None = None,
    go_module_dir: Path | None = None,
    python_distributions: Sequence[Mapping[str, str]] | None = None,
    go_modules: Sequence[Mapping[str, Any]] | None = None,
    npm_packages: Sequence[Mapping[str, Any]] | None = None,
    git_commit: str | None = None,
    created: str | None = None,
    namespace: str | None = None,
) -> dict[str, Any]:
    """Generate an SPDX 2.3 JSON document for a release artifact."""

    repo_root = Path(repo_root)
    artifact = Path(artifact)
    if not artifact.is_file() and not artifact.is_dir():
        raise MetadataError(f"Artifact must be a regular file or directory: {artifact}")
    findings = audit_package_content(artifact)
    if findings:
        detail = ", ".join(f"{item['path']} ({item['reason']})" for item in findings)
        raise MetadataError(f"Release artifact failed filename audit: {detail}")

    metadata = validate_owner_metadata(owner_metadata)
    effective_media_root = Path(media_root) if media_root else (artifact if artifact.is_dir() else artifact.parent)
    verify_media_tool_files(metadata, effective_media_root)
    version = read_canonical_version(repo_root)
    target_platform = _validate_target(target_platform, "platform")
    target_architecture = _validate_target(target_architecture, "architecture")

    if python_distributions is None:
        python_distributions = collect_python_distributions()
    if go_modules is None:
        go_modules = collect_go_modules(go_module_dir) if go_module_dir else []
    if npm_packages is None:
        npm_packages = collect_npm_packages(package_lock) if package_lock else []
    if git_commit is None:
        git_commit = get_git_commit(repo_root)
    if git_commit and not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", git_commit):
        raise MetadataError("Git commit must be a 40- or 64-character hexadecimal object ID")

    if created is None:
        created = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if namespace is None:
        namespace = (
            "https://spdx.org/spdxdocs/"
            f"vela-{quote(version, safe='.-')}-{uuid.uuid4()}"
        )

    artifact_hash = sha256_path(artifact)
    artifact_kind = "canonical directory tree" if artifact.is_dir() else "regular file"
    artifact_id = _spdx_id("Package", artifact.name, f"artifact:{artifact.name}:{version}")
    artifact_package = _package(
        name=artifact.name,
        spdx_id=artifact_id,
        version=version,
    )
    artifact_package.update(
        {
            "primaryPackagePurpose": "APPLICATION",
            "checksums": [{"algorithm": "SHA256", "checksumValue": artifact_hash}],
            "comment": (
                f"Target platform: {target_platform}\n"
                f"Target architecture: {target_architecture}\n"
                f"Artifact hash input: {artifact_kind}"
                + (f"\nGit commit: {git_commit.lower()}" if git_commit else "")
            ),
        }
    )

    packages: list[dict[str, Any]] = [artifact_package]
    files: list[dict[str, Any]] = []
    relationships: list[dict[str, str]] = [
        {
            "spdxElementId": "SPDXRef-DOCUMENT",
            "relationshipType": "DESCRIBES",
            "relatedSpdxElement": artifact_id,
        }
    ]

    for tool in metadata["media_tools"]:
        tool_identity = f"media:{tool['name']}@{tool['version']}"
        tool_id = _spdx_id("Package-MediaTool", tool["name"], tool_identity)
        tool_package = _package(
            name=tool["name"],
            spdx_id=tool_id,
            version=tool["version"],
            download_location=tool["source_url"],
            license_declared=tool["declared_license"],
        )
        tool_package.update(
            {
                "primaryPackagePurpose": "APPLICATION",
                "homepage": tool["upstream_url"],
                "sourceInfo": (
                    f"Build configuration: {tool['build_configuration']}\n"
                    f"Source offer/reference: {tool['source_offer']}"
                ),
            }
        )
        packages.append(tool_package)
        relationships.append(_dependency_relationship(artifact_id, tool_id))
        for media_file in tool["files"]:
            file_id = _spdx_id(
                "File-MediaTool",
                Path(media_file["path"]).name,
                f"{tool_identity}:{media_file['path']}:{media_file['sha256']}",
            )
            files.append(
                {
                    "fileName": f"./{media_file['path']}",
                    "SPDXID": file_id,
                    "checksums": [
                        {"algorithm": "SHA256", "checksumValue": media_file["sha256"]}
                    ],
                    "licenseConcluded": "NOASSERTION",
                    "licenseInfoInFiles": ["NOASSERTION"],
                    "copyrightText": "NOASSERTION",
                    "comment": (
                        f"Bundled file for {tool['name']} {tool['version']}; "
                        f"owner-declared package license: {tool['declared_license']}"
                    ),
                }
            )
            relationships.append(
                {
                    "spdxElementId": tool_id,
                    "relationshipType": "CONTAINS",
                    "relatedSpdxElement": file_id,
                }
            )

    normalized_python = sorted(
        {
            (str(item.get("name", "")).strip(), str(item.get("version", "")).strip())
            for item in python_distributions
            if item.get("name") and item.get("version")
        },
        key=lambda item: (item[0].casefold(), item[1]),
    )
    for name, dependency_version in normalized_python:
        dependency_id = _spdx_id(
            "Package-Python",
            name,
            f"python:{name.casefold()}@{dependency_version}",
        )
        package = _package(
            name=name,
            spdx_id=dependency_id,
            version=dependency_version,
        )
        package["primaryPackagePurpose"] = "LIBRARY"
        package["externalRefs"] = _purl_external_ref(
            f"pkg:pypi/{quote(name.casefold().replace('_', '-'), safe='.-')}@"
            f"{quote(dependency_version, safe='.+-')}"
        )
        packages.append(package)
        relationships.append(_dependency_relationship(artifact_id, dependency_id))

    normalized_go = sorted(
        [dict(item) for item in go_modules if item.get("path")],
        key=lambda item: (str(item["path"]).casefold(), str(item.get("version", ""))),
    )
    for module in normalized_go:
        name = str(module["path"])
        dependency_version = str(module.get("version", "")) or None
        identity = f"go:{name}@{dependency_version or '(main)'}"
        dependency_id = _spdx_id("Package-Go", name, identity)
        package = _package(name=name, spdx_id=dependency_id, version=dependency_version)
        package["primaryPackagePurpose"] = "LIBRARY"
        if dependency_version:
            package["externalRefs"] = _purl_external_ref(
                f"pkg:golang/{quote(name, safe='/.-')}@{quote(dependency_version, safe='.+-')}"
            )
        comments: list[str] = []
        if module.get("main"):
            comments.append("Main Go module")
        replacement = module.get("replace")
        if isinstance(replacement, Mapping) and replacement.get("path"):
            replacement_text = str(replacement["path"])
            if replacement.get("version"):
                replacement_text += f"@{replacement['version']}"
            comments.append(f"Replacement: {replacement_text}")
        if comments:
            package["comment"] = "\n".join(comments)
        packages.append(package)
        relationships.append(_dependency_relationship(artifact_id, dependency_id))

    normalized_npm = sorted(
        [dict(item) for item in npm_packages if item.get("name") and item.get("version")],
        key=lambda item: (
            str(item["name"]).casefold(),
            str(item["version"]),
            str(item.get("path", "")).casefold(),
        ),
    )
    for npm_package in normalized_npm:
        name = str(npm_package["name"])
        dependency_version = str(npm_package["version"])
        lock_path = str(npm_package.get("path", ""))
        identity = f"npm:{name}@{dependency_version}:{lock_path}"
        dependency_id = _spdx_id("Package-Npm", name, identity)
        package = _package(
            name=name,
            spdx_id=dependency_id,
            version=dependency_version,
            license_declared=_valid_declared_license(npm_package.get("license")),
        )
        package["primaryPackagePurpose"] = "LIBRARY"
        package["externalRefs"] = _purl_external_ref(
            f"pkg:npm/{quote(name, safe='/')}"
            f"@{quote(dependency_version, safe='.+-')}"
        )
        package["comment"] = (
            f"package-lock path: {lock_path or 'unknown'}; "
            f"development dependency: {'true' if npm_package.get('dev') else 'false'}"
        )
        packages.append(package)
        relationships.append(_dependency_relationship(artifact_id, dependency_id))

    return {
        "spdxVersion": SPDX_VERSION,
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"Vela {version} release SBOM ({artifact.name})",
        "documentNamespace": namespace,
        "creationInfo": {
            "created": created,
            "creators": [f"Tool: {TOOL_NAME}"],
        },
        "packages": packages,
        "files": sorted(files, key=lambda item: item["fileName"].casefold()),
        "relationships": relationships,
    }


def augment_spdx_document(
    *,
    base_document: Mapping[str, Any],
    repo_root: Path,
    artifact: Path,
    owner_metadata: Mapping[str, Any],
    target_platform: str,
    target_architecture: str,
    media_root: Path,
    git_commit: str | None = None,
) -> dict[str, Any]:
    """Add release-artifact and owner evidence to an SPDX 2.3 package scan."""

    if not isinstance(base_document, Mapping):
        raise MetadataError("Base SPDX document must be a JSON object")
    if base_document.get("spdxVersion") != SPDX_VERSION:
        raise MetadataError(f"Base SPDX document must use {SPDX_VERSION}")
    if base_document.get("SPDXID") != "SPDXRef-DOCUMENT":
        raise MetadataError("Base SPDX document must use SPDXRef-DOCUMENT")

    merged = json.loads(json.dumps(base_document))
    for field in ("packages", "files", "relationships"):
        value = merged.setdefault(field, [])
        if not isinstance(value, list):
            raise MetadataError(f"Base SPDX field {field} must be an array")

    creation_info = merged.get("creationInfo")
    if not isinstance(creation_info, dict):
        raise MetadataError("Base SPDX document must contain creationInfo")
    creators = creation_info.setdefault("creators", [])
    if not isinstance(creators, list):
        raise MetadataError("Base SPDX creationInfo.creators must be an array")
    owner_creator = f"Tool: {TOOL_NAME}"
    if owner_creator not in creators:
        creators.append(owner_creator)

    owner_document = generate_spdx_document(
        repo_root=Path(repo_root),
        artifact=Path(artifact),
        owner_metadata=owner_metadata,
        target_platform=target_platform,
        target_architecture=target_architecture,
        media_root=Path(media_root),
        python_distributions=[],
        go_modules=[],
        npm_packages=[],
        git_commit=git_commit,
        created=str(creation_info.get("created") or ""),
        namespace="https://spdx.org/spdxdocs/vela-owner-evidence",
    )

    existing_ids = {
        item.get("SPDXID")
        for field in ("packages", "files")
        for item in merged[field]
        if isinstance(item, Mapping)
    }
    owner_items = owner_document["packages"] + owner_document["files"]
    collisions = sorted(
        str(item["SPDXID"])
        for item in owner_items
        if item.get("SPDXID") in existing_ids
    )
    if collisions:
        raise MetadataError(f"Base SPDX identifier collision: {', '.join(collisions)}")

    merged["packages"].extend(owner_document["packages"])
    merged["files"].extend(owner_document["files"])
    merged["relationships"].extend(owner_document["relationships"])

    version = read_canonical_version(Path(repo_root))
    merged["name"] = f"Vela {version} release SBOM ({Path(artifact).name})"
    return merged


def _write_json(data: Mapping[str, Any], output: str) -> None:
    rendered = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if output == "-":
        sys.stdout.write(rendered)
        return
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(rendered, encoding="utf-8", newline="\n")


def _default_repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate SPDX 2.3 release metadata and audit release package names."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate", help="generate an SPDX 2.3 JSON SBOM")
    generate.add_argument("--artifact", required=True, help="built file or app directory")
    generate.add_argument("--owner-metadata", required=True, help="owner media-tool metadata JSON")
    generate.add_argument("--platform", required=True, dest="target_platform")
    generate.add_argument("--architecture", required=True, dest="target_architecture")
    generate.add_argument("--output", required=True, help="output path, or - for stdout")
    generate.add_argument("--repo-root", default=str(_default_repo_root()))
    generate.add_argument(
        "--media-root",
        help="root containing owner-declared media files (default: artifact or its parent)",
    )
    generate.add_argument("--package-lock", help="npm package-lock JSON path")
    generate.add_argument("--go-module-dir", help="directory containing go.mod")

    augment = subparsers.add_parser(
        "augment",
        help="add release artifact and owner evidence to a package-scan SPDX document",
    )
    augment.add_argument("--base-spdx", required=True, help="SPDX 2.3 JSON package scan")
    augment.add_argument("--artifact", required=True, help="final release artifact")
    augment.add_argument("--owner-metadata", required=True, help="owner media-tool metadata JSON")
    augment.add_argument("--media-root", required=True, help="root containing declared media files")
    augment.add_argument("--platform", required=True, dest="target_platform")
    augment.add_argument("--architecture", required=True, dest="target_architecture")
    augment.add_argument("--output", required=True, help="output path, or - for stdout")
    augment.add_argument("--repo-root", default=str(_default_repo_root()))

    audit = subparsers.add_parser("audit", help="audit package filenames for sensitive material")
    audit.add_argument("paths", nargs="+", help="regular files or app directories")
    audit.add_argument("--json", action="store_true", dest="json_output")

    validate = subparsers.add_parser(
        "validate-owner",
        help="validate owner-supplied media-tool metadata",
    )
    validate.add_argument("--metadata", required=True)
    validate.add_argument(
        "--media-root",
        help="also verify every declared file and SHA-256 below this root",
    )
    return parser


def _command_generate(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root)
    package_lock = (
        Path(args.package_lock)
        if args.package_lock
        else repo_root / "antra-wails" / "frontend" / "package-lock.json"
    )
    go_module_dir = (
        Path(args.go_module_dir)
        if args.go_module_dir
        else repo_root / "antra-wails"
    )
    document = generate_spdx_document(
        repo_root=repo_root,
        artifact=Path(args.artifact),
        owner_metadata=load_owner_metadata(Path(args.owner_metadata)),
        target_platform=args.target_platform,
        target_architecture=args.target_architecture,
        media_root=Path(args.media_root) if args.media_root else None,
        package_lock=package_lock,
        go_module_dir=go_module_dir,
    )
    _write_json(document, args.output)
    return 0


def _command_augment(args: argparse.Namespace) -> int:
    try:
        base_document = json.loads(Path(args.base_spdx).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MetadataError(f"Cannot read base SPDX document {args.base_spdx}: {exc}") from exc
    document = augment_spdx_document(
        base_document=base_document,
        repo_root=Path(args.repo_root),
        artifact=Path(args.artifact),
        owner_metadata=load_owner_metadata(Path(args.owner_metadata)),
        target_platform=args.target_platform,
        target_architecture=args.target_architecture,
        media_root=Path(args.media_root),
    )
    _write_json(document, args.output)
    return 0


def _command_audit(args: argparse.Namespace) -> int:
    all_findings: list[dict[str, str]] = []
    for raw_path in args.paths:
        audited_path = Path(raw_path)
        for finding in audit_package_content(audited_path):
            all_findings.append(
                {
                    "input": str(audited_path),
                    "path": finding["path"],
                    "reason": finding["reason"],
                }
            )
    all_findings.sort(key=lambda item: (item["input"].casefold(), item["path"].casefold()))
    if args.json_output:
        sys.stdout.write(json.dumps({"findings": all_findings}, indent=2, sort_keys=True) + "\n")
    elif all_findings:
        for finding in all_findings:
            print(
                f"{finding['input']}: {finding['path']}: {finding['reason']}",
                file=sys.stderr,
            )
    else:
        print("Filename audit passed.")
    return 1 if all_findings else 0


def _command_validate_owner(args: argparse.Namespace) -> int:
    metadata = load_owner_metadata(Path(args.metadata))
    if args.media_root:
        verify_media_tool_files(metadata, Path(args.media_root))
    file_count = sum(len(tool["files"]) for tool in metadata["media_tools"])
    suffix = " with verified hashes" if args.media_root else ""
    print(
        f"Owner metadata valid: {len(metadata['media_tools'])} tools, "
        f"{file_count} files{suffix}."
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "generate":
            return _command_generate(args)
        if args.command == "augment":
            return _command_augment(args)
        if args.command == "audit":
            return _command_audit(args)
        if args.command == "validate-owner":
            return _command_validate_owner(args)
    except (MetadataError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
