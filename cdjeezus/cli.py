"""CLI entry point for CDJeezus.

Where your SoundCloud sins meet their Soulseek salvation.
"""

import argparse
import asyncio
import logging
import sys
from logging.handlers import RotatingFileHandler

from .config import LOG_FILE, PID_FILE
from .style import banner, ok, fail, dim, accent, separator, boxed, console, press_enter


def _setup_logging(verbose: bool, daemon: bool = False) -> None:
    """Configure logging with console and optional file handler."""
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    datefmt = "%H:%M:%S"

    root = logging.getLogger()
    root.setLevel(level)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(logging.Formatter(fmt, datefmt))
    root.addHandler(console_handler)

    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            str(LOG_FILE), maxBytes=5 * 1024 * 1024, backupCount=3,
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(logging.Formatter(fmt, datefmt))
        root.addHandler(file_handler)
    except OSError as e:
        logging.warning("Could not set up file logging: %s", e)

    if not verbose:
        logging.getLogger("aioslsk.network.connection").setLevel(logging.CRITICAL)
        logging.getLogger("aioslsk.client").setLevel(logging.CRITICAL)
        for name in ("aioslsk.network.network", "aioslsk.tasks",
                     "aioslsk.shares.manager", "aioslsk.distributed",
                     "aioslsk.network.upnp", "aioslsk.search.manager"):
            logging.getLogger(name).setLevel(logging.ERROR)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="cdjeezus",
        description="CDJeezus — SoundCloud > Soulseek > Serato/Rekordbox pipeline",
    )
    subparsers = parser.add_subparsers(dest="command")

    parser.add_argument("-d", "--daemon", action="store_true", help="Run as persistent daemon (poll loop)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    parser.add_argument("--version", action="store_true", help="Print version and exit")
    parser.add_argument("--force", action="store_true", help="Force start even if another instance is running")

    setup_parser = subparsers.add_parser("setup", help="Run the setup wizard")
    setup_parser.add_argument("--non-interactive", action="store_true",
                              help="Non-interactive setup: use env vars or defaults for all prompts")

    update_parser = subparsers.add_parser("update", help="Update CDJeezus to the latest version")
    update_parser.add_argument("--check", action="store_true", help="Check for updates without installing")

    subparsers.add_parser("uninstall", help="Remove CDJeezus (safe for your library)")
    subparsers.add_parser("stop", help="Gracefully stop the running daemon")
    subparsers.add_parser("log", help="Show live log output from the running daemon")

    args = parser.parse_args()
    from . import __version__

    if args.version:
        banner(__version__)
        return

    # ── stop ──
    if args.command == "stop":
        from .daemon import request_stop
        console.print()
        boxed(f'CDJeezus v{__version__}', 'Telling the daemon to wrap it up...')
        stopped = request_stop(timeout=120)
        if stopped:
            ok("CDJeezus stopped. Go touch some real vinyl.")
        else:
            fail("Daemon didn't stop in time. Try again or use `cdjeezus stop`.")
        console.print()
        return

    # ── log ──
    if args.command == "log":
        from .daemon import tail_log
        tail_log()
        return

    # ── setup ──
    if args.command == "setup":
        from .setup import run_setup
        run_setup(non_interactive=getattr(args, "non_interactive", False))
        return

    # ── update ──
    if args.command == "update":
        from .updater import run_update
        run_update(check_only=getattr(args, 'check', False))
        return

    # ── uninstall ──
    if args.command == "uninstall":
        from .setup import full_uninstall
        full_uninstall()
        return

    # ── default run ──
    banner(__version__)

    from .daemon import is_running, tail_log

    existing_pid = is_running()
    if existing_pid and not args.force:
        accent(f"CDJeezus is already running (PID {existing_pid})")
        dim("Showing live output (Ctrl+C to detach):\n")
        tail_log()
        return

    from .setup import kill_running_daemon, INSTALLED_PLIST
    kill_running_daemon()

    if INSTALLED_PLIST.exists():
        import subprocess
        subprocess.run(["launchctl", "unload", str(INSTALLED_PLIST)], capture_output=True, check=False)
        subprocess.run(["launchctl", "load", str(INSTALLED_PLIST)], capture_output=True, check=False)

    _setup_logging(verbose=args.verbose, daemon=args.daemon)

    from .config import is_configured

    if not is_configured():
        console.print()
        banner(__version__)
        console.print()
        from .style import play_intro_rant
        play_intro_rant()
        separator()
        console.print("[bold cyan]Alright, let's get you set up.[/bold cyan]")
        separator()
        console.print()
        from .setup import run_setup
        run_setup(non_interactive=False)
        import importlib
        from . import config as _cfg
        importlib.reload(_cfg)
        if not _cfg.is_configured():
            fail("Setup didn't complete.")
            dim("Run `cdjeezus setup` when you're ready.")
            sys.exit(1)

    from .__main__ import amain

    try:
        asyncio.run(amain(daemon=args.daemon))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
