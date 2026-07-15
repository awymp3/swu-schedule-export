#!/usr/bin/env bash
# Build the six platform release archives from a clean source checkout.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUTPUT_DIR="${1:-$ROOT/dist}"
VERSION_FILE="$ROOT/VERSION"

release_version="$(sed -n 's/^Release: //p' "$VERSION_FILE" | head -n 1)"
build_id="$(sed -n 's/^Build: //p' "$VERSION_FILE" | head -n 1)"

if [ -z "$release_version" ] || [ -z "$build_id" ]; then
  echo "VERSION must contain both Release and Build values." >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required to build UTF-8 ZIP release packages." >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"
stage="$(mktemp -d "${TMPDIR:-/tmp}/swu-schedule-export.XXXXXX")"
trap 'rm -rf "$stage"' EXIT

common_files=(
  .env.example
  LICENSE
  README.md
  SECURITY.md
  THIRD_PARTY_NOTICES.md
  VERSION
  capture.py
  capture_auto.py
  index.html
)

build_package() {
  local platform="$1"
  local launcher="$2"
  local bundle="swu-schedule-export-${release_version}-${platform}"
  local bundle_dir="$stage/$bundle"
  local file

  rm -rf "$bundle_dir"
  mkdir -p "$bundle_dir"
  for file in "${common_files[@]}"; do
    cp "$ROOT/$file" "$bundle_dir/$file"
  done
  cp "$ROOT/$launcher" "$bundle_dir/$launcher"

  (
    cd "$stage"
    # macOS's built-in zip omits the UTF-8 filename flag.  Use the Python
    # standard library so Chinese launcher names extract correctly on Windows.
    python3 - "$bundle" "$OUTPUT_DIR/$bundle.zip" <<'PY'
import os
import sys
import zipfile

bundle, output = sys.argv[1:]
with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    for root, dirs, files in os.walk(bundle):
        dirs.sort()
        files.sort()
        relative_root = os.path.relpath(root, ".")
        archive.write(root, relative_root + "/")
        for filename in files:
            path = os.path.join(root, filename)
            archive.write(path, os.path.relpath(path, "."))
PY
    # Do not include macOS resource-fork sidecar files (._*) in cross-platform archives.
    COPYFILE_DISABLE=1 tar -czf "$OUTPUT_DIR/$bundle.tar.gz" "$bundle"
  )
}

build_package "windows" "启动-Windows.bat"
build_package "macos" "启动-Mac.command"
build_package "linux" "启动-Linux.sh"

echo "Release: $release_version"
echo "Build:   $build_id"
echo "Output:  $OUTPUT_DIR"
printf '%s\n' "$OUTPUT_DIR"/*
