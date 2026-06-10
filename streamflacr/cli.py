"""CLI entry point for StreamFLACr."""

import argparse
import asyncio
import logging
import sys

from .setup import run_setup, register_launchdaemon, unregister_launchdaemon


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="streamflacr",
        description="StreamFLACr - Auto-download FLAC from Soulseek for SoundCloud playlist additions",
    )
    subparsers = parser.add_subparsers(dest="command")

    # Default run command (no subcommand)
    parser.add_argument("-d", "--daemon", action="store_true", help="Run as persistent daemon (poll loop)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")

    # setup subcommand
    setup_parser = subparsers.add_parser("setup", help="Run interactive setup wizard")
    setup_parser.add_argument("--uninstall", action="store_true", help="Unregister LaunchDaemon and remove config")

    args = parser.parse_args()

    if args.command == "setup":
        if args.uninstall:
            unregister_launchdaemon()
            return
        run_setup()
        return

    from .config import is_configured

    if not is_configured():
        print("\n  Welcome to StreamFLACr! Let's get you set up.\n")
        run_setup()
        # Reload config module so module-level vars pick up the new .env
        import importlib
        from . import config as _cfg
        importlib.reload(_cfg)
        if not _cfg.is_configured():
            print("\n  Setup incomplete. Run `streamflacr setup` to try again.\n")
            sys.exit(1)

    # Run the main daemon
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    from .__main__ import amain
    try:
        asyncio.run(amain(daemon=args.daemon))
    except KeyboardInterrupt:
        print("\n  Stopped.")


if __name__ == "__main__":
    main()
