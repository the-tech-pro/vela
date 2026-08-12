#!/usr/bin/env bash
#
# Build the Vela-audited iPod 6G Rockbox helper from corresponding source.
# Run on a disposable Linux host or WSL environment. This script never writes
# to an iPod and never installs the resulting archive automatically.
#
set -euo pipefail

ROCKBOX_REPOSITORY="https://git.rockbox.org/cgit/rockbox.git"
ROCKBOX_COMMIT="2df1172e985c45e9bf7fe3283bbb42dfaa36c735"
OLSRO_REVISION="1f3d33805259c1c2b58a5076bb3580e86bacdaf1"
OLSRO_PATCH_URL="https://raw.githubusercontent.com/Olsro/reddit-ipod-guides/${OLSRO_REVISION}/guides/ipod6g-flash-more-recent-firmwares/rockbox-2df1172-ipod6g%3A%20add%20SysCFG%20flashing%20tools%20from%20the%20debug%20menu.patch"
OLSRO_PATCH_SHA256="6eb01128105d875d24db8828f1b4f73250279527fb71d161c42aee1e5924feac"
VELA_PATCH_SHA256="321a42df1247d05acc1332468b4afb845ede9c304694a60e1380f1a63deab405"

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
WORK_DIR="${1:-${PWD}/rockbox-helper-work}"
OUTPUT_DIR="${2:-${PWD}/rockbox-helper-output}"
TOOLCHAIN_PREFIX="${ROCKBOX_TOOLCHAIN_PREFIX:-${WORK_DIR}/toolchain}"
JOBS="${JOBS:-2}"

fail() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

for command in git curl sha256sum patch make tar gzip perl; do
    command -v "$command" >/dev/null 2>&1 || fail "required command is missing: $command"
done

case "$JOBS" in
    ''|*[!0-9]*) fail "JOBS must be a positive integer" ;;
    0) fail "JOBS must be greater than zero" ;;
esac

[[ ! -e "$WORK_DIR" ]] || fail "work directory already exists: $WORK_DIR"
[[ ! -e "$OUTPUT_DIR" ]] || fail "output directory already exists: $OUTPUT_DIR"

mkdir -p "$WORK_DIR" "$OUTPUT_DIR"
SOURCE_DIR="${WORK_DIR}/rockbox"
OLSRO_PATCH="${WORK_DIR}/olsro-syscfg.patch"
VELA_PATCH="${SCRIPT_DIR}/vela-rockbox-syscfg-readback.patch"
BUILD_DIR="${WORK_DIR}/build-ipod6g"

printf 'Cloning Rockbox corresponding source...\n'
git clone --no-tags --no-checkout "$ROCKBOX_REPOSITORY" "$SOURCE_DIR"
git -C "$SOURCE_DIR" checkout --detach "$ROCKBOX_COMMIT"
[[ "$(git -C "$SOURCE_DIR" rev-parse HEAD)" == "$ROCKBOX_COMMIT" ]] \
    || fail "Rockbox checkout does not match the pinned commit"

printf 'Downloading and verifying the pinned Olsro source patch...\n'
curl --proto '=https' --tlsv1.2 --location --fail --silent --show-error \
    "$OLSRO_PATCH_URL" --output "$OLSRO_PATCH"
printf '%s  %s\n' "$OLSRO_PATCH_SHA256" "$OLSRO_PATCH" | sha256sum --check --status \
    || fail "Olsro patch hash mismatch"
printf '%s  %s\n' "$VELA_PATCH_SHA256" "$VELA_PATCH" | sha256sum --check --status \
    || fail "Vela readback patch hash mismatch"

git -C "$SOURCE_DIR" apply --check "$OLSRO_PATCH"
git -C "$SOURCE_DIR" apply "$OLSRO_PATCH"
git -C "$SOURCE_DIR" apply --check "$VELA_PATCH"
git -C "$SOURCE_DIR" apply "$VELA_PATCH"

if ! command -v arm-elf-eabi-gcc >/dev/null 2>&1 \
    && [[ ! -x "${TOOLCHAIN_PREFIX}/bin/arm-elf-eabi-gcc" ]]; then
    printf 'Building the pinned Rockbox ARM cross-toolchain. This can take a long time...\n'
    "$SOURCE_DIR/tools/rockboxdev.sh" --target="a" "--prefix=${TOOLCHAIN_PREFIX}"
fi
export PATH="${TOOLCHAIN_PREFIX}/bin:${PATH}"
command -v arm-elf-eabi-gcc >/dev/null 2>&1 \
    || fail "Rockbox ARM cross-compiler is unavailable"

mkdir -p "$BUILD_DIR"
(
    cd "$BUILD_DIR"
    "$SOURCE_DIR/tools/configure" \
        --target=ipod6g \
        --type=n \
        "--prefix=${TOOLCHAIN_PREFIX}"
    make "-j${JOBS}"
    make fullzip
)

HELPER_ARCHIVE="${BUILD_DIR}/rockbox-full.zip"
[[ -f "$HELPER_ARCHIVE" ]] || fail "Rockbox build did not produce rockbox-full.zip"
cp "$HELPER_ARCHIVE" "${OUTPUT_DIR}/vela-ipod6g-syscfg-helper.zip"

CORRESPONDING_DIR="${WORK_DIR}/corresponding-source"
mkdir -p "${CORRESPONDING_DIR}/rockbox"
git -C "$SOURCE_DIR" archive "$ROCKBOX_COMMIT" \
    | tar -xf - -C "${CORRESPONDING_DIR}/rockbox"
cp "$OLSRO_PATCH" "${CORRESPONDING_DIR}/olsro-syscfg.patch"
cp "$VELA_PATCH" "${CORRESPONDING_DIR}/vela-rockbox-syscfg-readback.patch"
cp "${SCRIPT_DIR}/artifact-lock.json" "${CORRESPONDING_DIR}/artifact-lock.json"
cp "${SCRIPT_DIR}/build-rockbox-helper.sh" "${CORRESPONDING_DIR}/build-rockbox-helper.sh"
cp "${SCRIPT_DIR}/README.md" "${CORRESPONDING_DIR}/README.md"
cp "${SCRIPT_DIR}/licenses/GPL-2.0-or-later.txt" \
    "${CORRESPONDING_DIR}/GPL-2.0-or-later.txt"
cp "${SCRIPT_DIR}/licenses/Olsro-MIT.txt" "${CORRESPONDING_DIR}/Olsro-MIT.txt"

tar --sort=name --mtime='UTC 1970-01-01' --owner=0 --group=0 --numeric-owner \
    -czf "${OUTPUT_DIR}/vela-ipod6g-helper-corresponding-source.tar.gz" \
    -C "$CORRESPONDING_DIR" .

HELPER_SHA256="$(sha256sum "${OUTPUT_DIR}/vela-ipod6g-syscfg-helper.zip" | cut -d' ' -f1)"
SOURCE_SHA256="$(sha256sum "${OUTPUT_DIR}/vela-ipod6g-helper-corresponding-source.tar.gz" | cut -d' ' -f1)"
GCC_VERSION="$(arm-elf-eabi-gcc --version)"
GCC_VERSION="${GCC_VERSION%%$'\n'*}"

cat >"${OUTPUT_DIR}/BUILD-MANIFEST.txt" <<EOF
Rockbox repository: ${ROCKBOX_REPOSITORY}
Rockbox commit: ${ROCKBOX_COMMIT}
Target: ipod6g
Olsro guide revision: ${OLSRO_REVISION}
Olsro patch SHA-256: ${OLSRO_PATCH_SHA256}
Vela readback patch SHA-256: ${VELA_PATCH_SHA256}
Helper archive SHA-256: ${HELPER_SHA256}
Corresponding source SHA-256: ${SOURCE_SHA256}
Compiler: ${GCC_VERSION}
License: GPL-2.0-or-later
EOF

printf 'Build complete. Review and publish all three output files together:\n'
printf '  %s\n' \
    "${OUTPUT_DIR}/vela-ipod6g-syscfg-helper.zip" \
    "${OUTPUT_DIR}/vela-ipod6g-helper-corresponding-source.tar.gz" \
    "${OUTPUT_DIR}/BUILD-MANIFEST.txt"
