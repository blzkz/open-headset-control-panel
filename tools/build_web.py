"""Assemble the WebHID web app into a static folder (GitHub Pages).

    python3 tools/build_web.py [OUT_DIR]      (default: build/web)

Model data comes from ``openlogi/models.py`` so the desktop app and the web
share one registry; product images are converted to WebP when Pillow exists.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from openlogi import __version__  # noqa: E402
from openlogi.models import ALL_PIDS, MODELS  # noqa: E402
from openlogi.presets import BUILTIN_PRESETS  # noqa: E402

try:
    from PIL import Image
except ImportError:  # still produces a working site, just with heavier PNGs
    Image = None


def build(out: Path) -> None:
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(ROOT / "web", out)
    images = out / "assets" / "images"
    images.mkdir(parents=True, exist_ok=True)

    used = {m.image for m in MODELS} | {"headset-side.png"}
    image_map: dict[str, str] = {}
    for name in sorted(used):
        src = ROOT / "openlogi" / "assets" / "images" / name
        if Image is not None:
            dst = images / (Path(name).stem + ".webp")
            with Image.open(src) as im:
                im.save(dst, "WEBP", quality=88, method=6)
        else:
            dst = images / name
            shutil.copyfile(src, dst)
        image_map[name] = f"assets/images/{dst.name}"
    shutil.copyfile(ROOT / "openlogi" / "assets" / "icons" / "openlogi-headset.svg", out / "assets" / "icon.svg")
    shutil.copyfile(ROOT / "packaging" / "70-openlogi-headset.rules", out / "70-openlogi-headset.rules")

    data = {
        "version": __version__,
        "vendorId": 0x046D,
        "pids": list(ALL_PIDS),
        "models": [
            {
                "key": m.key,
                "name": m.name,
                "pids": list(m.pids),
                "image": image_map[m.image],
                "curve": [list(p) for p in m.curve],
                "wireless": m.wireless,
                "verified": m.verified,
                "lightZones": [list(z) for z in m.light_zones],
                "chargerPid": m.charger_pid,
            }
            for m in MODELS
        ],
        "presets": [{"key": p.key, "name": p.name, "gains": list(p.gains)} for p in BUILTIN_PRESETS],
        "sideImage": image_map["headset-side.png"],
    }
    (out / "data").mkdir(exist_ok=True)
    (out / "data" / "models.json").write_text(json.dumps(data, indent=1))
    (out / ".nojekyll").write_text("")
    print(f"web app built in {out} ({sum(f.stat().st_size for f in out.rglob('*') if f.is_file()) // 1024} KiB)")


if __name__ == "__main__":
    build(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "build" / "web")
