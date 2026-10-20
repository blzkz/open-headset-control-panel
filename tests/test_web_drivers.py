"""The web app's JavaScript driver ports must match HeadsetControl too (tests/web/golden.mjs)."""

from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless(shutil.which("node"), "Node.js not installed")
class WebDrivers(unittest.TestCase):
    def test_javascript_ports_match_headsetcontrol(self) -> None:
        res = subprocess.run(["node", str(ROOT / "tests" / "web" / "golden.mjs")], capture_output=True, text=True,
                             timeout=600)
        self.assertEqual(res.returncode, 0, res.stdout[-4000:] + res.stderr[-2000:])
        self.assertIn("0 failing", res.stdout)


if __name__ == "__main__":
    unittest.main()
