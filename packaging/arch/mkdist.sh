#!/usr/bin/env bash
# Build the source tarballs that the Arch PKGBUILDs consume.
#
#   dist/spmlinux-0.1.0.tar.gz            -> the native CLI
#   dist/spektrum-programmer-3.10.6.0.tar.gz -> the Windows payload (Wine)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT="$ROOT/packaging/arch/dist"
VERSION_PKG=0.1.0
VERSION_APP=3.10.6.0

mkdir -p "$OUT"

# ---------------------------------------------------------------- native CLI
rm -rf "$OUT/stage"
mkdir -p "$OUT/stage/spmlinux-$VERSION_PKG"
cp "$ROOT/pyproject.toml" "$OUT/stage/spmlinux-$VERSION_PKG/"
cp -r "$ROOT/spmlinux" "$OUT/stage/spmlinux-$VERSION_PKG/spmlinux"
rm -rf "$OUT/stage/spmlinux-$VERSION_PKG/spmlinux/__pycache__" \
       "$OUT/stage/spmlinux-$VERSION_PKG/spmlinux/tests/__pycache__"
cp "$ROOT/REPORT.md" "$OUT/stage/spmlinux-$VERSION_PKG/REPORT.md"
mkdir -p "$OUT/stage/spmlinux-$VERSION_PKG/udev"
cp "$ROOT/packaging/arch/udev/99-spektrum-ftdi.rules" \
   "$OUT/stage/spmlinux-$VERSION_PKG/udev/"
tar -C "$OUT/stage" -czf "$OUT/spmlinux-$VERSION_PKG.tar.gz" "spmlinux-$VERSION_PKG"
rm -rf "$OUT/stage"

# ------------------------------------------------------------ Windows payload
SRC="$ROOT/nsis_extract"
if [ ! -d "$SRC" ]; then
    echo "error: $SRC not found - extract the installer first:" >&2
    echo "  7z x spektrum-programmer-3.10.6.0.exe -onsis_extract" >&2
    exit 1
fi
rm -rf "$OUT/stage"
mkdir -p "$OUT/stage/spektrum-programmer-$VERSION_APP"
# everything the app needs at runtime; the Windows *drivers* (ftdibus.sys,
# dpinst, ...) are useless on Linux and are left out
for item in SpmProgrammer.exe Images DefaultModels Tooltips.txt; do
    [ -e "$SRC/$item" ] && cp -r "$SRC/$item" "$OUT/stage/spektrum-programmer-$VERSION_APP/"
done
cp "$SRC"/*.dll "$OUT/stage/spektrum-programmer-$VERSION_APP/" 2>/dev/null || true
tar -C "$OUT/stage" -czf "$OUT/spektrum-programmer-$VERSION_APP.tar.gz" \
    "spektrum-programmer-$VERSION_APP"
rm -rf "$OUT/stage"

echo "built:"
ls -la "$OUT"
