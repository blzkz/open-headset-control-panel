"""Updater logic that doesn't need the network."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from openheadset import updater
from openheadset.updater import InstallKind, Release

ASSETS = {name: f"https://api.github.com/assets/{i}" for i, name in enumerate([
    "open-headset-1.2.0-1-any.pkg.tar.zst",
    "open-headset-1.2.0-1.noarch.rpm",
    "open-headset_1.2.0-1_all.deb",
    "Open_Headset_Control_Panel-1.2.0-x86_64.AppImage",
    "openheadset_headset-1.2.0-py3-none-any.whl",
    "SHA256SUMS",
])}


class UpdaterTests(unittest.TestCase):
    def test_version_compare(self) -> None:
        self.assertTrue(updater.is_newer("v1.0.10", "1.0.9"))
        self.assertTrue(updater.is_newer("1.1.0", "1.0.99"))
        self.assertFalse(updater.is_newer("1.0.4", "1.0.4"))
        self.assertFalse(updater.is_newer("1.0.3", "1.0.4"))

    def test_asset_per_install_kind(self) -> None:
        rel = Release("1.2.0", "v1.2.0", "", "", dict(ASSETS))
        self.assertTrue(rel.asset_for(InstallKind.PACMAN).endswith(".pkg.tar.zst"))
        self.assertTrue(rel.asset_for(InstallKind.DEB).endswith("_all.deb"))
        self.assertTrue(rel.asset_for(InstallKind.RPM).endswith(".noarch.rpm"))
        self.assertTrue(rel.asset_for(InstallKind.APPIMAGE).endswith(".AppImage"))
        self.assertIsNone(rel.asset_for(InstallKind.SOURCE))

    def test_install_commands_use_pkexec(self) -> None:
        pkg = Path("/tmp/x.pkg")
        self.assertEqual(updater.install_command(InstallKind.PACMAN, pkg)[:3], ["pkexec", "pacman", "-U"])
        with mock.patch.object(updater.shutil, "which", side_effect=lambda c: c == "zypper"):
            cmd = updater.install_command(InstallKind.RPM, pkg)
        self.assertEqual(cmd[:2], ["pkexec", "zypper"])
        self.assertIn("--allow-unsigned-rpm", cmd)

    def test_unverified_downloads_are_refused(self) -> None:
        rel = Release("1.2.0", "v1.2.0", "", "", {k: v for k, v in ASSETS.items() if k != "SHA256SUMS"})
        with self.assertRaises(updater.UpdateError):
            updater.download_verified(rel, InstallKind.PACMAN)

    def test_appimage_is_replaced_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            current = Path(d) / "OpenHeadset.AppImage"
            current.write_bytes(b"old")
            new = Path(d) / "download.AppImage"
            new.write_bytes(b"new")
            with mock.patch.dict(os.environ, {"APPIMAGE": str(current)}):
                updater.install(InstallKind.APPIMAGE, new)
                self.assertEqual(updater.detect_install(), InstallKind.APPIMAGE)
            self.assertEqual(current.read_bytes(), b"new")
            self.assertTrue(os.access(current, os.X_OK))

    def test_non_https_is_rejected(self) -> None:
        with self.assertRaises(updater.UpdateError):
            updater._request("http://example.com/x", accept="*/*", token=None)


if __name__ == "__main__":
    unittest.main()
