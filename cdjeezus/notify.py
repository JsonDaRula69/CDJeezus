"""macOS notification support."""

import logging
import subprocess

logger = logging.getLogger(__name__)


def send_notification(title: str, message: str) -> None:
    """Send a macOS notification via osascript."""
    # Escape for AppleScript
    title_escaped = title.replace('"', '\\"').replace("\\", "\\\\")
    message_escaped = message.replace('"', '\\"').replace("\\", "\\\\")
    script = f'display notification "{message_escaped}" with title "{title_escaped}"'
    try:
        subprocess.run(["osascript", "-e", script], check=True, capture_output=True, timeout=5)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.warning("Could not send notification: %s", e)
