#!/usr/bin/env bash
# Wrap the PyInstaller output (dist/TCG Catalog/) into a single AppImage file.
#   packaging/linux/make_appimage.sh 1.2.3
# Needs appimagetool on PATH (the GitHub workflow downloads it).
set -euo pipefail
cd "$(dirname "$0")/../.."
version="${1:-0.0.0}"
appdir=build/AppDir
rm -rf "$appdir"
mkdir -p "$appdir/usr/lib"
cp -a "dist/TCG Catalog" "$appdir/usr/lib/tcg-catalog"
cp assets/icon.png "$appdir/tcg-catalog.png"
cat > "$appdir/tcg-catalog.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=TCG Catalog
Comment=Scan, sort and browse your trading cards
Exec=tcg-catalog
Icon=tcg-catalog
Terminal=false
Categories=Utility;
EOF
cat > "$appdir/AppRun" <<'EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/lib/tcg-catalog/TCG Catalog" "$@"
EOF
chmod +x "$appdir/AppRun"
out="dist/TCG-Catalog-${version}-Linux-x86_64.AppImage"
ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 appimagetool "$appdir" "$out"
echo "$out"
