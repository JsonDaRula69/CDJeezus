"""CDJeezus terminal style — because if you're spending four figures on a
single deck that doesn't even have Stems yet, the least we can do is make
your CLI look nice while it saves your library.

ANSI color constants, box-drawing helpers, formatted output, progress
spinners, and the intro rant. Windows-compatible: gates ANSI features
behind _supports_ansi() and _IS_WINDOWS checks.
"""

import os
import sys
import time

_IS_WINDOWS = os.name == "nt"


def _no_color() -> bool:
    """Check if the user wants colors suppressed."""
    return (
        os.environ.get("NO_COLOR", "") != ""
        or os.environ.get("TERM", "") == "dumb"
    )


def _supports_ansi() -> bool:
    """Whether we can safely emit ANSI escape sequences."""
    if _no_color():
        return False
    if _IS_WINDOWS:
        return True
    return True


# ── ANSI codes ──────────────────────────────────────────────────────────

RST = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
ITALIC = "\033[3m"
UNDERLINE = "\033[4m"
BLINK = "\033[5m"
REVERSE = "\033[7m"

# Foreground
BLACK = "\033[30m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"
WHITE = "\033[37m"

# Bright foreground
BRIGHT_RED = "\033[91m"
BRIGHT_GREEN = "\033[92m"
BRIGHT_YELLOW = "\033[93m"
BRIGHT_BLUE = "\033[94m"
BRIGHT_MAGENTA = "\033[95m"
BRIGHT_CYAN = "\033[96m"
BRIGHT_WHITE = "\033[97m"

# Dim foreground (grey)
DIM_WHITE = "\033[90m"
DIM_RED = "\033[31m\033[2m"
DIM_GREEN = "\033[32m\033[2m"
DIM_YELLOW = "\033[33m\033[2m"
DIM_BLUE = "\033[34m\033[2m"
DIM_CYAN = "\033[36m\033[2m"

# CDJeezus palette aliases
AMBER = YELLOW
BRIGHT_AMBER = BRIGHT_YELLOW

# Background
BG_BLACK = "\033[40m"
BG_RED = "\033[41m"
BG_GREEN = "\033[42m"
BG_YELLOW = "\033[43m"
BG_BLUE = "\033[44m"
BG_MAGENTA = "\033[45m"
BG_CYAN = "\033[46m"
BG_WHITE = "\033[47m"
BG_BRIGHT_BLACK = "\033[100m"


# ── Color wrappers ───────────────────────────────────────────────────────

def c(code: str, text: str) -> str:
    """Wrap text in an ANSI color code (suppressed if NO_COLOR)."""
    if not _supports_ansi():
        return text
    return f"{code}{text}{RST}"


def dim(text: str) -> str:
    return c(DIM, text)


def bold(text: str) -> str:
    return c(BOLD, text)


def italic(text: str) -> str:
    return c(ITALIC, text)


def underline(text: str) -> str:
    return c(UNDERLINE, text)


def header(text: str) -> str:
    return c(BRIGHT_CYAN, bold(text))


def success(text: str) -> str:
    return c(BRIGHT_GREEN, f"  \u2713 {text}")


def warning(text: str) -> str:
    return c(BRIGHT_AMBER, f"  \u26a0 {text}")


def error(text: str) -> str:
    return c(BRIGHT_RED, f"  \u2717 {text}")


def info(text: str) -> str:
    return c(DIM_WHITE, text)


def accent(text: str) -> str:
    return c(BRIGHT_AMBER, text)


def cyan_label(text: str) -> str:
    """Cyan text for labels/keys in summaries."""
    return c(CYAN, text)


def amber_value(text: str) -> str:
    """Amber text for values in summaries."""
    return c(BRIGHT_AMBER, text)


def red_label(text: str) -> str:
    """Red for warnings, disabled states."""
    return c(BRIGHT_RED, text)


# ── Box-drawing characters ──────────────────────────────────────────────
# Unicode box-drawing — widely supported in modern terminals on all platforms.
# Fallback: plain ASCII chars on terminals that can't render them.

BOX_TL = "\u250c"   # ┌
BOX_TR = "\u2510"   # ┐
BOX_BL = "\u2514"   # └
BOX_BR = "\u2518"   # ┘
BOX_H  = "\u2500"   # ─
BOX_V  = "\u2502"   # │
BOX_LT = "\u251c"   # ├
BOX_RT = "\u2524"   # ┤
BOX_T  = "\u252c"   # ┬
BOX_B  = "\u253c"   # ┼
BLOCK   = "\u2588"   # █
DARK_BLOCK = "\u2591" # ░
MED_BLOCK  = "\u2592" # ▒
LIGHT_BLOCK = "\u2593" # ▓

# Progress bar blocks
BAR_FULL  = "\u2588"  # █
BAR_PARTS = ["\u258f", "\u258e", "\u258d", "\u258c", "\u258b", "\u258a", "\u2589"]


def separator(width: int = 54, char: str = BOX_H) -> str:
    """Horizontal separator line using box-drawing or plain chars."""
    if not _supports_ansi():
        return "  " + "-" * width
    return c(DIM, "  " + char * width)


def box(title: str = "", width: int = 54) -> str:
    """Draw a box frame, optionally with a title.

    Returns the top border as a string. Use box_bottom() for closure.
    """
    if not _supports_ansi():
        return "  " + "+" + ("-" * width) + "+"
    title_display = f" {title} " if title else ""
    title_len = len(title_display)
    inner = width - 2
    if title_len > 0 and title_len < inner:
        left = (inner - title_len) // 2
        right = inner - title_len - left
        line = BOX_TL + (BOX_H * left) + title_display + (BOX_H * right) + BOX_TR
    else:
        line = BOX_TL + (BOX_H * inner) + BOX_TR
    return c(DIM, "  " + line)


def box_bottom(width: int = 54) -> str:
    """Close a box frame."""
    if not _supports_ansi():
        return "  " + "+" + ("-" * width) + "+"
    return c(DIM, "  " + BOX_BL + (BOX_H * (width - 2)) + BOX_BR)


def box_mid(width: int = 54) -> str:
    """Middle separator inside a box."""
    if not _supports_ansi():
        return "  " + "|" + ("-" * (width - 2)) + "|"
    return c(DIM, "  " + BOX_LT + (BOX_H * (width - 2)) + BOX_RT)


def box_line(text: str, width: int = 54) -> str:
    """A line of text inside a box frame, with padding."""
    inner = width - 4  # box sides + padding
    if not _supports_ansi():
        return "  | " + text.ljust(inner) + "|"
    return c(DIM, "  " + BOX_V + " ") + text.ljust(inner) + c(DIM, BOX_V)


def kv_line(key: str, value: str, width: int = 54) -> str:
    """Key-value pair on a box line, with the key in cyan and value in amber."""
    inner = width - 4
    raw_text = f"{key}: {value}"
    if not _supports_ansi():
        return "  | " + raw_text.ljust(inner) + "|"
    styled = f"{cyan_label(key)}: {amber_value(value)}"
    # Pad with spaces to fill width (account for ANSI codes being invisible)
    visible_len = len(raw_text)
    padding = max(0, inner - visible_len)
    return c(DIM, "  " + BOX_V + " ") + styled + (" " * padding) + c(DIM, BOX_V)


def progress_bar(current: int, total: int, width: int = 20, label: str = "") -> str:
    """Render a progress bar using Unicode block characters."""
    if total <= 0:
        pct = 0
    else:
        pct = current / total
    filled = int(width * pct)
    empty = width - filled
    bar = c(BRIGHT_CYAN, BAR_FULL * filled) + c(DIM, DARK_BLOCK * empty)
    pct_text = f"{pct * 100:.0f}%"
    if label:
        return f"  {bar} {pct_text} {dim(label)}"
    return f"  {bar} {pct_text}"


# ── Step indicator ───────────────────────────────────────────────────────

def step_header(step: int, total: int, label: str) -> str:
    """Formatted step header for the setup wizard.

    '[2/8] Checking Soulseek...' with color coding.
    """
    step_text = f"[{step}/{total}]"
    if not _supports_ansi():
        return f"  {step_text} {label}"
    return f"  {c(CYAN, bold(step_text))} {c(BRIGHT_WHITE, label)}"


# ── Spinner ─────────────────────────────────────────────────────────────

_SPINNER_FRAMES = ["\u28d9", "\u28f5", "\u28eb", "\u28b6", "\u2867", "\u29c7", "\u25d0", "\u25d1"]
# Fallback simpler spinner for terminals without good Unicode support
_SPINNER_SIMPLE = ["|", "/", "-", "\\"]


def spinner_frame(index: int) -> str:
    """Return a spinner character for the given frame index."""
    if not _supports_ansi():
        return _SPINNER_SIMPLE[index % len(_SPINNER_SIMPLE)]
    return _SPINNER_FRAMES[index % len(_SPINNER_FRAMES)]


# ── Menu cursors for simple_term_menu ────────────────────────────────────

MENU_CURSOR = "\u25b6 "       # ▶ (cyan arrow)
MULTI_SELECT_ON = "\u2611 "   # ☑
MULTI_SELECT_OFF = "\u2610 "  # ☐

# simple_term-menu style tuples (fg_cyan, fg_yellow, etc.)
MENU_CURSOR_STYLE = ("fg_cyan", "bold")
MENU_HIGHLIGHT_STYLE = ("fg_yellow", "bold")
MULTI_SELECT_CURSOR_STYLE = ("fg_cyan", "bold")
MULTI_SELECT_BRACKETS_STYLE = ("fg_gray",)
SEARCH_HIGHLIGHT_STYLE = ("fg_black", "bg_cyan", "bold")
STATUS_BAR_STYLE = ("fg_cyan", "bg_black")

# Sarcasm-flavored hints
MULTI_SELECT_HINT = "space to select, enter to confirm, esc to nope out"
SEARCH_HINT = "type / to search, because scrolling is for DJs without prep"


# ── ASCII art banner ────────────────────────────────────────────────────

BANNER_ART = (
    f"{CYAN}{BOLD}"
    f"   ██████╗██████╗ ██████╗ ███████╗\n"
    f"  ██╔════╝██╔══██╗██╔══██╗██╔════╝\n"
    f"  ██║     ██████╔╝██████╔╝███████╗\n"
    f"  ██║     ██╔══██╗██╔═══╝ ╚════██║\n"
    f"  ╚██████╗██║  ██║██║     ███████║\n"
    f"   ╚═════╝╚═╝  ╚═╝╚═╝     ╚══════╝{RST}"
)

TAGLINE = "They said I can't bring my Numark, so I guess we're going old school again"

BANNER = BANNER_ART + f"\n        {BRIGHT_AMBER}{ITALIC}{TAGLINE}{RST}"


def format_banner(version: str) -> str:
    """Return the colored banner with version number."""
    if not _supports_ansi():
        return f"CDJeezus v{version}\n  {TAGLINE}"
    return BANNER + f"\n        {c(DIM_WHITE, 'v' + version)}"


# ── Intro rant (first-time launch only) ──────────────────────────────────

INTRO_RANT = (
    "I gotta reformat my entire damn library because they refuse to let me "
    "bring my beautiful Numark NS7iii that has actual spinning vinyls that "
    "let me cut, scratch, spin, and actually feel the music and tempo. How "
    "does a CDJ somehow feel cheap and still be expensive as fuck at the "
    "same time. How is it so expensive and still be behind on features like "
    "Stems. Why do they even have an autosync button if it actually never "
    "works anyways, and why do people insist on syncing manually when no one "
    "except other DJs are gonna notice anyways. Do you also drive a car "
    "without power steering to prove you're a big strong man?\n\n"
    "You still turned quantize on though, I saw you.\n\n"
    "Anyways, at least it got me to make this software, it's pretty cool. "
    "You're welcome and if anyone asks, your music comes from BeatSource "
    "cause you're a professional and we respect artists."
)


def play_intro_rant(duration: float = 5.0) -> None:
    """Type out the intro rant character by character, then clear it.

    Falls back to a simple print on Windows or non-interactive terminals.
    The animation takes `duration` seconds. After finishing, waits 3
    seconds, then clears the rant and returns.
    """
    is_interactive = sys.stdout.isatty()

    if not _supports_ansi() or not is_interactive:
        print(dim(INTRO_RANT))
        time.sleep(2)
        return

    total_chars = len(INTRO_RANT)
    delay = duration / total_chars

    # Print the rant in CDJeezus amber
    rant_color = BRIGHT_AMBER
    sys.stdout.write(rant_color)
    try:
        for ch in INTRO_RANT:
            sys.stdout.write(ch)
            sys.stdout.flush()
            if ch in ".?!":
                time.sleep(delay * 8)
            elif ch == ",":
                time.sleep(delay * 4)
            elif ch == "\n":
                time.sleep(delay * 6)
            else:
                time.sleep(delay)
    except KeyboardInterrupt:
        sys.stdout.write(RST + "\n")
        return

    sys.stdout.write(RST + "\n")
    sys.stdout.flush()

    # Let them read the punchline
    time.sleep(3)

    # Clear the rant: move cursor up and wipe
    lines = INTRO_RANT.count("\n") + 2
    if not _IS_WINDOWS:
        sys.stdout.write(f"\033[{lines}F\033[J")
    else:
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            h = kernel32.GetStdHandle(-11)
            kernel32.SetConsoleCursorPosition(h, ctypes._0 * 0 + 0)
            sys.stdout.write(f"\033[{lines}F\033[J")
        except Exception:
            sys.stdout.write("\n" * lines)
    sys.stdout.flush()


# ── Countdown ────────────────────────────────────────────────────────────

def countdown(seconds: int, message: str = "Retrying") -> None:
    """Display a countdown with animated text.

    Shows 'Retrying in 15s... 14s... 13s...' on a single line.
    """
    is_interactive = sys.stdout.isatty()
    for i in range(seconds, 0, -1):
        if not is_interactive or not _supports_ansi():
            print(f"  {message} in {i}s...")
            time.sleep(1)
            continue
        # Overwrite the same line
        sys.stdout.write(f"\r  {c(BRIGHT_AMBER, message)} in {c(BRIGHT_CYAN, str(i) + 's')}...  ")
        sys.stdout.flush()
        time.sleep(1)
    if is_interactive and _supports_ansi():
        sys.stdout.write("\r" + " " * 60 + "\r")
        sys.stdout.flush()


# ── Formatted messages ──────────────────────────────────────────────────

def format_step(step: int, total: int, label: str) -> str:
    """Format a setup step header with CDJeezus styling."""
    return step_header(step, total, label)


def format_boxed(title: str, lines: list[str], width: int = 54) -> str:
    """Return a full box with title and content lines."""
    parts = [box(title, width)]
    for line in lines:
        parts.append(box_line(line, width))
    parts.append(box_bottom(width))
    return "\n".join(parts)


def format_kv_box(title: str, pairs: list[tuple[str, str]], width: int = 54) -> str:
    """Return a box with key-value summary pairs."""
    parts = [box(title, width)]
    for key, value in pairs:
        parts.append(kv_line(key, value, width))
    parts.append(box_bottom(width))
    return "\n".join(parts)
