#!/usr/bin/env bash
# Build every native package into dist/.  Needs `nfpm` in PATH (https://nfpm.goreleaser.com).
#   packaging/build-packages.sh [VERSION]
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION="${1:-$(python3 -c 'import openlogi; print(openlogi.__version__)')}"
VERSION="${VERSION#v}"
export VERSION
packaging/stage.sh "$VERSION"
mkdir -p dist
for fmt in deb rpm archlinux; do
    nfpm package --config packaging/nfpm.yaml --packager "$fmt" --target dist/
done
ls -l dist/
