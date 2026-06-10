"""CLI entry point for StreamFLACr."""

import argparse
import asyncio
import logging
import sys

from .setup import run_setup, register_launchdaemon


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="streamflacr",
        description="StreamFLACr - Auto-download FLAC from Soulseek for SoundCloud playlist additions",
    )
    subparsers = parser.add_subparsers(dest="command")

    # Default run command (no subcommand)
    parser.add_argument("-d", "--daemon", action="store_true", help="Run as persistent daemon (poll loop)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    parser.add_argument("--version", action="store_true", help="Print version and exit")

    # setup subcommand
    setup_parser = subparsers.add_parser("setup", help="Run interactive setup wizard")

    # uninstall subcommand
    subparsers.add_parser("uninstall", help="Remove all StreamFLACr artifacts (safe for Serato)")

    args = parser.parse_args()

    if args.version:
        from . import __version__
        print(f"StreamFLACr v{__version__}")
        return

    if args.command == "setup":
        run_setup()
        return

    if args.command == "uninstall":
        from .setup import full_uninstall
        full_uninstall()
        return

    from .config import is_configured

    if not is_configured():
        from . import __version__
        print(f"\n  Welcome to StreamFLACr v{__version__}! Let's get you set up.\n")
        run_setup()
        # Reload config module so module-level vars pick up the new .env
        import importlib
        from . import config as _cfg
        importlib.reload(_cfg)
        if not _cfg.is_configured():
            print("\n  Setup incomplete. Run `streamflacr setup` to try again.\n")
            sys.exit(1)

    # Kill any stale daemon before starting (avoids port conflicts)
    from .setup import kill_running_daemon
    kill_running_daemon()

    # Run the main daemon
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Quiet aioslsk internals: ConnectToPeer failures, port binding errors,
    # and peer connection noise are normal Soulseek network chatter.
    # Port binding tracebacks from aioslsk.network.connection are CRITICAL-only
    # since we gracefully handle that in soulseek.py's connect() fallback.
    if not args.verbose:
        logging.getLogger("aioslsk.network.connection").setLevel(logging.CRITICAL)
        for name in ("aioslsk.network.network", "aioslsk.tasks",
                     "aioslsk.shares.manager", "aioslsk.distributed",
                     "aioslsk.network.upnp", "aioslsk.search.manager"):
            logging.getLogger(name).setLevel(logging.ERROR)

    from .__main__ import amain
    try:
        asyncio.run(amain(daemon=args.daemon))
    except KeyboardInterrupt:
        pass
    print("\n  Stopped.")


if __name__ == "__main__":
    main()
