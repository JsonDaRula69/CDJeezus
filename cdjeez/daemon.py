"""Daemon lifecycle management: PID tracking, stop signaling, single-instance."""

import logging
import os
import signal
import time

from .config import PID_FILE, STOP_FILE, LOG_FILE
from .style import dim, accent, console

logger = logging.getLogger(__name__)


def write_pid() -> None:
    """Write the current process PID to the PID file."""
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))


def read_pid() -> int | None:
    """Read PID from the PID file. Returns None if missing or invalid."""
    if not PID_FILE.exists():
        return None
    try:
        return int(PID_FILE.read_text().strip())
    except (ValueError, OSError):
        return None


def remove_pid() -> None:
    """Remove the PID file."""
    try:
        PID_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def is_running() -> int | None:
    """Check if a CDJeez instance is running.

    Returns the PID of the running instance, or None.
    Cleans up stale PID files automatically.
    """
    pid = read_pid()
    if pid is None:
        return None
    try:
        os.kill(pid, 0)
        return pid
    except ProcessLookupError:
        remove_pid()
        return None
    except PermissionError:
        return pid


def request_stop(timeout: int = 120) -> bool:
    """Signal the running daemon to stop gracefully.

    Writes the stop-requested flag, sends SIGUSR1 to wake the daemon,
    then waits up to `timeout` seconds for the process to exit.
    Also unloads the LaunchAgent to prevent auto-restart.
    Returns True if the daemon stopped, False if it's still running.
    """
    pid = is_running()
    if pid is None:
        remove_pid()
        clear_stop_flag()
        _unload_launchagent()
        return True

    # Write stop flag so the daemon sees it on next check
    STOP_FILE.parent.mkdir(parents=True, exist_ok=True)
    STOP_FILE.write_text("requested")

    # Send SIGUSR1 to wake the daemon from any sleep
    try:
        os.kill(pid, signal.SIGUSR1)
    except (ProcessLookupError, PermissionError):
        pass

    # Wait for the process to exit
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        try:
            os.kill(pid, 0)
            time.sleep(1)
        except ProcessLookupError:
            break

    # Clean up
    remove_pid()
    clear_stop_flag()

    # Unload LaunchAgent so it doesn't auto-restart
    _unload_launchagent()

    try:
        os.kill(pid, 0)
        return False  # Still running after timeout
    except ProcessLookupError:
        return True


def should_stop() -> bool:
    """Check if a graceful stop has been requested (called by the daemon)."""
    return STOP_FILE.exists()


def clear_stop_flag() -> None:
    """Remove the stop-requested flag file."""
    try:
        STOP_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def _unload_launchagent() -> None:
    """Unload the LaunchAgent plist to prevent auto-restart after a stop."""
    import subprocess
    from .setup import INSTALLED_PLIST
    for plist in (INSTALLED_PLIST,):
        if plist.exists():
            subprocess.run(
                ["launchctl", "unload", str(plist)],
                capture_output=True, check=False,
            )


def tail_log() -> None:
    """Tail the CDJeez log file to show live output.

    Blocks until the user presses Ctrl+C.
    """
    if not LOG_FILE.exists():
        dim("No log file found. Is the daemon running?")
        return

    import subprocess
    accent("Showing live output")
    dim("Ctrl+C to detach")
    console.print()
    try:
        subprocess.run(["tail", "-f", str(LOG_FILE)], check=False)
    except KeyboardInterrupt:
        console.print()
        dim("Detached.")
