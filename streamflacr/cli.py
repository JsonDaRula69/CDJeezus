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
    setup_parser.add_argument("--non-interactive", action="store_true",
                              help="Non-interactive setup: use env vars or defaults for all prompts")

    # update subcommand
    update_parser = subparsers.add_parser("update", help="Update StreamFLACr to the latest version")
    update_parser.add_argument("--check", action="store_true", help="Check for updates without installing")

    # uninstall subcommand
    subparsers.add_parser("uninstall", help="Remove all StreamFLACr artifacts (safe for Serato)")

    args = parser.parse_args()

    from . import __version__

    if args.version:
        print(f"StreamFLACr v{__version__}")
        return

    if args.command == "setup":
        non_interactive = getattr(args, "non_interactive", False)
        run_setup(non_interactive=non_interactive)
        return

    # Print version as the very first output so it appears right after install
    if args.command == "update":
        from .updater import run_update
        check_only = getattr(args, 'check', False)
        run_update(check_only=check_only)
        return

    if args.command == "uninstall":
        print(f"  StreamFLACr v{__version__}")
        from .setup import full_uninstall
        full_uninstall()
        return

    if args.command != "setup":
        print(f"StreamFLACr v{__version__}")

    from .config import is_configured

    if not is_configured():
        print(f"  Welcome to StreamFLACr v{__version__}! Let's get you set up.")
        run_setup(non_interactive=False)
        # Reload config module so module-level vars pick up the new .env
        import importlib
        from . import config as _cfg
        importlib.reload(_cfg)
        if not _cfg.is_configured():
            print("\n  Setup incomplete. Run `streamflacr setup` to try again.\n")
            sys.exit(1)

    # Kill any stale daemon and reload the LaunchAgent so upgrades take effect
    from .setup import kill_running_daemon, INSTALLED_PLIST
    kill_running_daemon()
    # If a LaunchAgent plist exists, reload it so the daemon picks up the new version
    if INSTALLED_PLIST.exists():
        import subprocess
        subprocess.run(["launchctl", "unload", str(INSTALLED_PLIST)], capture_output=True, check=False)
        subprocess.run(["launchctl", "load", str(INSTALLED_PLIST)], capture_output=True, check=False)

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
        logging.getLogger("aioslsk.client").setLevel(logging.CRITICAL)
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
