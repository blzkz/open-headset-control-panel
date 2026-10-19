#!/usr/bin/env bash
# Build a self-contained AppImage (CPython from python-appimage + PyQt6 wheels).
#   packaging/appimage/build.sh [VERSION]
set -euo pipefail
cd "$(dirname "$0")/../.."
VERSION="${1:-$(python3 -c 'import openlogi; print(openlogi.__version__)')}"
VERSION="${VERSION#v}"
PYVER="${PYVER:-3.12}"
ARCH="${ARCH:-x86_64}"
WORK="build/appimage"
CACHE="${APPIMAGE_CACHE:-build/cache}"
mkdir -p "$WORK" "$CACHE" dist

py_url="$(curl -sfL "https://api.github.com/repos/niess/python-appimage/releases/tags/python$PYVER" \
    | grep -o "https://[^\"]*manylinux_2_28_$ARCH.AppImage" | head -n1)"
tool_url="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-$ARCH.AppImage"
[ -f "$CACHE/python.AppImage" ] || curl -sfL -o "$CACHE/python.AppImage" "$py_url"
[ -f "$CACHE/appimagetool.AppImage" ] || curl -sfL -o "$CACHE/appimagetool.AppImage" "$tool_url"
chmod +x "$CACHE/python.AppImage" "$CACHE/appimagetool.AppImage"

rm -rf -- "${WORK:?}/AppDir" "${WORK:?}/squashfs-root"
(cd "$WORK" && APPIMAGE_EXTRACT_AND_RUN=1 "../../$CACHE/python.AppImage" --appimage-extract >/dev/null)
mv "$WORK/squashfs-root" "$WORK/AppDir"
APPDIR="$WORK/AppDir"
PY="$(ls -d "$APPDIR"/opt/python3.*/bin/python3.* | grep -E 'python3\.[0-9]+$' | head -n1)"

# app + PyQt6 (pinned to the Qt6 wheels that ship QtMultimedia/QtSvg and the Wayland/xcb plugins)
"$PY" -s -m pip install --no-cache-dir --no-warn-script-location --upgrade pip >/dev/null
"$PY" -s -m pip install --no-cache-dir --no-warn-script-location "PyQt6>=6.7" .
sed -i "s/^__version__ = .*/__version__ = \"$VERSION\"/" \
    "$(dirname "$PY")"/../lib/python3.*/site-packages/openlogi/__init__.py
# trim: tests, caches and Qt bits we never load
find "$APPDIR" -name '__pycache__' -type d -prune -exec rm -rf {} +
QT="$(ls -d "$(dirname "$PY")"/../lib/python3.*/site-packages/PyQt6/Qt6)"
rm -rf "$QT"/qml "$QT"/translations "$QT"/lib/libQt6WebEngine* "$QT"/lib/libQt6Quick3D* \
       "$QT"/lib/libQt6Pdf* "$QT"/lib/libQt6Designer* "$QT"/lib/libQt6Qml* "$QT"/lib/libQt6Quick* 2>/dev/null || true

# AppDir metadata
rm -f "$APPDIR"/*.desktop "$APPDIR"/*.png "$APPDIR"/*.svg "$APPDIR"/.DirIcon "$APPDIR"/AppRun
rm -rf "$APPDIR"/usr/share/applications "$APPDIR"/usr/share/metainfo
install -m755 packaging/appimage/AppRun "$APPDIR/AppRun"
install -Dm644 packaging/openlogi-headset.desktop "$APPDIR/openlogi-headset.desktop"
sed -i 's/^Exec=.*/Exec=openlogi-headset/' "$APPDIR/openlogi-headset.desktop"
install -m644 openlogi/assets/icons/openlogi-headset.svg "$APPDIR/openlogi-headset.svg"
ln -sf openlogi-headset.svg "$APPDIR/.DirIcon"
install -Dm644 packaging/io.github.blzkz.OpenLogiHeadset.metainfo.xml \
    "$APPDIR/usr/share/metainfo/io.github.blzkz.OpenLogiHeadset.appdata.xml"
sed -i "s/@VERSION@/$VERSION/" "$APPDIR/usr/share/metainfo/io.github.blzkz.OpenLogiHeadset.appdata.xml"

OUT="dist/OpenLogi_Headset-$VERSION-$ARCH.AppImage"
ARCH="$ARCH" APPIMAGE_EXTRACT_AND_RUN=1 "$CACHE/appimagetool.AppImage" --no-appstream "$APPDIR" "$OUT"
ls -l "$OUT"
