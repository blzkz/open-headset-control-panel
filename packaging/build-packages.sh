#!/usr/bin/env bash
# Build every native package into dist/.  Needs `nfpm` in PATH (https://nfpm.goreleaser.com).
#   packaging/build-packages.sh [VERSION]
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION="${1:-$(python3 -c 'import openlogi; print(openlogi.__version__)')}"
VERSION="${VERSION#v}"
export VERSION
packaging/stage.sh "$VERSION"
python3 - <<'PY'
from pathlib import Path

stage = Path("build/stage")
lines = []
for root in ("usr/share/openlogi-headset", "usr/share/doc/openlogi-headset"):
    base = stage / root
    for path in sorted([base, *base.rglob("*")]):
        dst = "/" + str(path.relative_to(stage))
        if path.is_dir():
            lines += [f"  - dst: {dst}", "    type: dir", "    file_info: { mode: 0755 }"]
        else:
            lines += [f"  - src: {path}", f"    dst: {dst}", "    file_info: { mode: 0644 }"]
template = Path("packaging/nfpm.yaml").read_text()
Path("build/nfpm.yaml").write_text(template.replace("  # @TREE@", "\n".join(lines)))
PY
mkdir -p dist
for fmt in deb rpm archlinux; do
    nfpm package --config build/nfpm.yaml --packager "$fmt" --target dist/
done
ls -l dist/
