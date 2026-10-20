#!/usr/bin/env bash
# Stage a distro-independent install tree in build/stage (consumed by nfpm and the AppImage).
#   packaging/stage.sh [VERSION]
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION="${1:-$(python3 -c 'import openheadset; print(openheadset.__version__)')}"
STAGE=build/stage
rm -rf -- "${STAGE:?}"
mkdir -p "$STAGE/usr/bin" "$STAGE/usr/share/open-headset" \
    "$STAGE/usr/share/applications" "$STAGE/usr/share/icons/hicolor/scalable/apps" \
    "$STAGE/usr/share/metainfo" "$STAGE/usr/lib/udev/rules.d" "$STAGE/usr/share/doc/open-headset"

cp -r openheadset "$STAGE/usr/share/open-headset/"
find "$STAGE" -name '__pycache__' -type d -prune -exec rm -rf {} +
sed -i "s/^__version__ = .*/__version__ = \"$VERSION\"/" "$STAGE/usr/share/open-headset/openheadset/__init__.py"

for pair in "open-headset:" "open-headsetctl:ctl "; do
    name="${pair%%:*}"; sub="${pair#*:}"
    cat > "$STAGE/usr/bin/$name" <<SH
#!/bin/sh
# Open Headset Control Panel launcher (the Python package lives outside site-packages so the
# same package works with every distro's Python version).
PYTHONPATH="/usr/share/open-headset\${PYTHONPATH:+:\$PYTHONPATH}" exec python3 -m openheadset ${sub}"\$@"
SH
    chmod 755 "$STAGE/usr/bin/$name"
done

install -m644 packaging/open-headset.desktop "$STAGE/usr/share/applications/"
install -m644 openheadset/assets/icons/open-headset.svg "$STAGE/usr/share/icons/hicolor/scalable/apps/"
install -m644 packaging/io.github.blzkz.OpenHeadsetControlPanel.metainfo.xml "$STAGE/usr/share/metainfo/"
install -m644 packaging/70-open-headset.rules "$STAGE/usr/lib/udev/rules.d/"
install -m644 README.md LICENSE "$STAGE/usr/share/doc/open-headset/"
sed -i "s/@VERSION@/$VERSION/" "$STAGE/usr/share/metainfo/io.github.blzkz.OpenHeadsetControlPanel.metainfo.xml"
echo "staged $VERSION in $STAGE"
