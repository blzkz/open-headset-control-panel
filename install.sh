#!/usr/bin/env bash
# Per-user install (no root needed except for the optional udev rule).
#   ./install.sh            install / update
#   ./install.sh --uninstall
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${XDG_DATA_HOME:-$HOME/.local/share}"
APPDIR="$PREFIX/openlogi-headset"
BINDIR="$HOME/.local/bin"
RULE=/etc/udev/rules.d/70-openlogi-headset.rules

if [[ "${1:-}" == "--uninstall" ]]; then
    rm -rf -- "${APPDIR:?}"
    rm -f -- "$BINDIR/openlogi-headset" "$BINDIR/openlogi-headsetctl" \
        "$PREFIX/applications/openlogi-headset.desktop" \
        "$PREFIX/icons/hicolor/scalable/apps/openlogi-headset.svg" \
        "$HOME/.config/autostart/openlogi-headset.desktop"
    echo "Uninstalled. (Config kept in ~/.config/openlogi-headset; udev rule: sudo rm $RULE)"
    exit 0
fi

python3 -c 'import PyQt6.QtWidgets, PyQt6.QtSvg, PyQt6.QtMultimedia' 2>/dev/null || {
    echo "PyQt6 (with QtSvg and QtMultimedia) is required, e.g.: sudo pacman -S python-pyqt6" >&2
    exit 1
}

mkdir -p "$APPDIR" "$BINDIR" "$PREFIX/applications" "$PREFIX/icons/hicolor/scalable/apps"
rm -rf -- "${APPDIR:?}/openlogi"
cp -r "$SRC/openlogi" "$APPDIR/"
find "$APPDIR" -name '__pycache__' -type d -prune -exec rm -rf {} +

for pair in "openlogi-headset:" "openlogi-headsetctl:ctl "; do
    name="${pair%%:*}"; sub="${pair#*:}"
    cat > "$BINDIR/$name" <<SH
#!/bin/sh
PYTHONPATH="$APPDIR\${PYTHONPATH:+:\$PYTHONPATH}" exec python3 -m openlogi ${sub}"\$@"
SH
    chmod +x "$BINDIR/$name"
done

sed "s|^Exec=.*|Exec=$BINDIR/openlogi-headset|" "$SRC/packaging/openlogi-headset.desktop" \
    > "$PREFIX/applications/openlogi-headset.desktop"
cp "$SRC/openlogi/assets/icons/openlogi-headset.svg" "$PREFIX/icons/hicolor/scalable/apps/"
command -v update-desktop-database >/dev/null && update-desktop-database "$PREFIX/applications" || true

echo "Installed to $APPDIR"
echo "  GUI: openlogi-headset      CLI: openlogi-headsetctl --help"

if [[ ! -f "$RULE" ]]; then
    echo
    echo "Optional: install the udev rule so the headset is accessible without root:"
    echo "  sudo install -m644 '$SRC/packaging/70-openlogi-headset.rules' $RULE && sudo udevadm control --reload && sudo udevadm trigger"
fi
