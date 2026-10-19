"""Entry point: ``python -m openlogi`` (GUI) or ``python -m openlogi ctl ...`` (CLI)."""

from __future__ import annotations

import argparse
import logging
import sys

from . import __version__


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("ctl", "cli"):
        from .cli import main as cli_main

        return cli_main(argv[1:])

    parser = argparse.ArgumentParser(prog="openlogi-headset", description="Logitech PRO X Wireless control panel")
    parser.add_argument("--minimized", action="store_true", help="start hidden in the system tray")
    parser.add_argument("--page", choices=["equalizer", "mic", "surround", "info"], help="open a device page")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--selftest", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.selftest:
        from .ui.app import selftest

        return selftest()
    from .ui.app import run

    return run(minimized=args.minimized, page=args.page)


if __name__ == "__main__":
    sys.exit(main())
