"""CLI entry point for StreamFLACr."""

import argparse
import asyncio
import logging
import sys
from logging.handlers import RotatingFileHandler

from .config import LOG_FILE, PID_FILE


def _setup_logging(verbose: bool, daemon: bool = False) -> None:
    """Configure logging with console and optional file handler."""
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    datefmt = "%H:%M:%S"

    root = logging.getLogger()
    root.setLevel(level)

    # Console handler
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(logging.Formatter(fmt, datefmt))
    root.addHandler(console)

    # File handler (rotating, 5MB max, 3 backups)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            str(LOG_FILE),
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(logging.Formatter(fmt, datefmt))
        root.addHandler(file_handler)
    except OSError as e:
        # If we can't write to the log file, just use console
        logging.warning("Could not set up file logging: %s", e)

    # Quiet aioslsk internals
    if not verbose:
        logging.getLogger("aioslsk.network.connection").setLevel(logging.CRITICAL)
        logging.getLogger("aioslsk.client").setLevel(logging.CRITICAL)
        for name in ("aioslsk.network.network", "aioslsk.tasks",
                     "aioslsk.shares.manager", "aioslsk.distributed",
                     "aioslsk.network.upnp", "aioslsk.search.manager"):
            logging.getLogger(name).setLevel(logging.ERROR)


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
    parser.add_argument("--force", action="store_true", help="Force start even if another instance is running")

    # setup subcommand
    setup_parser = subparsers.add_parser("setup", help="Run interactive setup wizard")
    setup_parser.add_argument("--non-interactive", action="store_true",
                              help="Non-interactive setup: use env vars or defaults for all prompts")

    # update subcommand
    update_parser = subparsers.add_parser("update", help="Update StreamFLACr to the latest version")
    update_parser.add_argument("--check", action="store_true", help="Check for updates without installing")

    # uninstall subcommand
    subparsers.add_parser("uninstall", help="Remove all StreamFLACr artifacts (safe for Serato)")

    # stop subcommand
    subparsers.add_parser("stop", help="Gracefully stop the running daemon")

    # log subcommand
    subparsers.add_parser("log", help="Show live log output from the running daemon")

    args = parser.parse_args()

    from . import __version__

    if args.version:
        print(f"StreamFLACr v{__version__}")
        return

    # ── stop command ──────────────────────────────────────────────────
    if args.command == "stop":
        from .daemon import request_stop
        print(f"  StreamFLACr v{__version__}")
        print("  Stopping daemon...")
        stopped = request_stop(timeout=120)
        if stopped:
            print("  ✓ StreamFLACr stopped.")
        else:
            print("  ✗ Daemon did not stop within timeout. Try again or use `streamflacr stop`.")
        return

    # ── log command ────────────────────────────────────────────────────
    if args.command == "log":
        from .daemon import tail_log
        tail_log()
        return

    # ── setup command ──────────────────────────────────────────────────
    if args.command == "setup":
        from .setup import run_setup
        non_interactive = getattr(args, "non_interactive", False)
        run_setup(non_interactive=non_interactive)
        return

    # ── update command ─────────────────────────────────────────────────
    if args.command == "update":
        from .updater import run_update
        check_only = getattr(args, 'check', False)
        run_update(check_only=check_only)
        return

    # ── uninstall command ──────────────────────────────────────────────
    if args.command == "uninstall":
        print(f"  StreamFLACr v{__version__}")
        from .setup import full_uninstall
        full_uninstall()
        return

    # ── default run (one-shot or daemon) ───────────────────────────────
    print(f"StreamFLACr v{__version__}")

    from .daemon import is_running, tail_log

    # Check if another instance is already running
    existing_pid = is_running()
    if existing_pid and not args.force:
        print(f"  StreamFLACr is already running (PID {existing_pid})")
        print(f"  Showing live output (Ctrl+C to detach):\n")
        tail_log()
        return

    # Kill any stale daemon process (but not the one we just checked above,
    # which shouldn't exist at this point unless --force was used)
    from .setup import kill_running_daemon, INSTALLED_PLIST
    kill_running_daemon()

    # If a LaunchAgent plist exists, reload it so upgrades take effect
    if INSTALLED_PLIST.exists():
        import subprocess
        subprocess.run(["launchctl", "unload", str(INSTALLED_PLIST)], capture_output=True, check=False)
        subprocess.run(["launchctl", "load", str(INSTALLED_PLIST)], capture_output=True, check=False)

    # Set up logging
    _setup_logging(verbose=args.verbose, daemon=args.daemon)

    from .config import is_configured

    if not is_configured():
        print(f"  Welcome to StreamFLACr v{__version__}! Let's get you set up.")
        from .setup import run_setup
        run_setup(non_interactive=False)
        # Reload config module so module-level vars pick up the new .env
        import importlib
        from . import config as _cfg
        importlib.reload(_cfg)
        if not _cfg.is_configured():
            print("\n  Setup incomplete. Run `streamflacr setup` to try again.\n")
            sys.exit(1)

    from .__main__ import amain

    try:
        asyncio.run(amain(daemon=args.daemon))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
