"""CDJeez terminal style — powered by rich + questionary.

All rendering goes through rich.Console (cross-platform, Windows/macOS/Linux).
All interactive prompts go through questionary (arrow keys, space, enter).
No manual ANSI codes, no termios dependency, no _IS_WINDOWS gates.

The intro rant typing animation is the only custom bit left, and it
falls back gracefully on non-interactive terminals.
"""

import os
import shutil
import sys
import time

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.rule import Rule
from rich.box import HEAVY, ROUNDED
from rich.style import Style
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn

import questionary

# ── Shared console ──────────────────────────────────────────────────────
# NO_COLOR is respected by rich automatically (via os.environ)
console = Console()

# ── Questionary style (CDJeez palette: cyan pointer, amber highlights) ──
QUESTIONARY_STYLE = questionary.Style([
    ('qmark', 'fg:#00ffff bold'),
    ('question', 'bold'),
    ('answer', 'fg:#ffdd00 bold'),
    ('pointer', 'fg:#00ffff bold'),
    ('highlighted', 'fg:#ffdd00 bold'),
    ('selected', 'fg:#666666'),
    ('checked', 'fg:#00ffff'),
    ('unchecked', 'fg:#666666'),
])


# ── Output helpers ──────────────────────────────────────────────────────

GO_BACK = "← Go back"


def banner(version: str) -> None:
    """Print the CDJeez ASCII banner with tagline and version."""
    art = Text()
    art.append('  /$$$$$$  /$$$$$$$     /$$$$$                              \n', style='bold green')
    art.append(" /$$__  $$| $$__  $$   |__  $$                              \n", style='bold green')
    art.append('| $$  \\__/| $$  \\ $$      | $$  /$$$$$$   /$$$$$$  /$$$$$$$$\n', style='bold green')
    art.append('| $$      | $$  | $$      | $$ /$$__  $$ /$$__  $$|____ /$$/\n', style='bold green')
    art.append('| $$      | $$  | $$ /$$  | $$| $$$$$$$$| $$$$$$$$   /$$$$/ \n', style='bold green')
    art.append('| $$    $$| $$  | $$| $$  | $$| $$_____/| $$_____/  /$$__/  \n', style='bold green')
    art.append('|  $$$$$$/| $$$$$$$/|  $$$$$$/|  $$$$$$$|  $$$$$$$ /$$$$$$$$\n', style='bold green')
    art.append(' \\______/ |_______/  \\______/  \\_______/ \\_______/|________/\n', style='bold green')
    art.append('\n        ', style='')
    art.append("They said I can't bring my Numark, so I guess we're going old school again",
              style='italic yellow')
    art.append(f'\n        v{version}', style='dim')
    console.print(art)


def step(step_num: int, total: int, label: str) -> None:
    """Print a colored step header like [2/8] Doing the thing..."""
    console.print(f'[bold cyan][{step_num}/{total}][/bold cyan] [bold white]{label}[/bold white]')


def ok(text: str) -> None:
    """Success message with green check."""
    console.print(f'  [bold green]\u2713[/bold green] {text}')


def warn(text: str) -> None:
    """Warning message with amber triangle."""
    console.print(f'  [bold yellow]\u26a0[/bold yellow] {text}')


def fail(text: str) -> None:
    """Error message with red cross."""
    console.print(f'  [bold red]\u2717[/bold red] {text}')


def dim(text: str) -> None:
    """Dim/secondary text."""
    console.print(f'  [dim]{text}[/dim]')


def quip(text: str) -> None:
    """Comedy aside — italic and dimmed so it reads like a footnote."""
    console.print(f'  [dim italic]{text}[/dim italic]')


def info(text: str) -> None:
    """Info/neutral text."""
    console.print(f'  {text}')


def accent(text: str) -> None:
    """Amber accent text."""
    console.print(f'  [yellow]{text}[/yellow]')


def separator() -> None:
    """Horizontal rule."""
    console.print(Rule(style='dim'))


def boxed(title: str, content: str, border: str = 'cyan') -> None:
    """Print a panel with a title. Content can be plain text or rich markup."""
    box_type = HEAVY if border == 'cyan' else ROUNDED
    console.print(Panel(content, title=f'[bold {border}]{title}[/bold {border}]',
                        border_style=border, box=box_type, padding=(0, 2)))


def summary_box(title: str, pairs: list[tuple[str, str]]) -> None:
    """Print a key-value summary in a panel with a table inside."""
    table = Table(show_header=False, box=None, padding=(0, 2), expand=False)
    table.add_column(style='cyan bold', justify='right')
    table.add_column(style='yellow', justify='left')
    for key, value in pairs:
        table.add_row(key, value)
    console.print(Panel(table, title=f'[bold cyan]{title}[/bold cyan]',
                        border_style='cyan', padding=(0, 1)))


def disclaimer_box(content: str) -> None:
    """Print the legal disclaimer in a rounded panel with amber border."""
    console.print(Panel(content,
                        title='[bold yellow]Legal Disclaimer (yeah, I know)[/bold yellow]',
                        border_style='yellow', box=ROUNDED, padding=(1, 2)))


def progress_bar(iterable=None, description: str = 'Working...', total: int | None = None):
    """Context manager for a rich progress bar.

    Usage:
        with progress_bar(items, description='Downloading FLACs...') as prog:
            for item in prog:
                ...
    """
    return Progress(
        SpinnerColumn(),
        TextColumn(f'[cyan]{{task.description}}[/cyan]'),
        BarColumn(bar_width=20),
        TextColumn('[yellow]{task.percentage:>3.0f}%[/yellow]'),
        TimeElapsedColumn(),
        console=console,
    )


# ── Interactive prompts (via questionary) ─────────────────────────────────

def select(prompt: str, choices: list[str], *, allow_back: bool = False) -> int:
    """Single-select menu (arrow keys + enter). Returns index of choice.

    If allow_back is True, a "← Go back" option is appended. Selecting it returns -1.
    Falls back to numbered input on non-interactive terminals.
    """
    effective = list(choices)
    if allow_back:
        effective.append(GO_BACK)

    if not sys.stdout.isatty():
        # Non-interactive fallback
        print(f"\n  {prompt}")
        for i, choice in enumerate(effective):
            print(f"    {i+1}. {choice}")
        while True:
            try:
                ans = int(input(f"  Enter number [1-{len(effective)}]: ")) - 1
                if 0 <= ans < len(effective):
                    if allow_back and ans == len(effective) - 1:
                        return -1
                    return ans
            except (ValueError, EOFError):
                pass

    result = questionary.select(
        f'  {prompt}',
        choices=effective,
        style=QUESTIONARY_STYLE,
    ).ask()

    if result is None:
        return 0  # Default to first on escape
    if result == GO_BACK:
        return -1
    return choices.index(result)


def multiselect(prompt: str, choices: list[str], *, allow_back: bool = False) -> list[int]:
    """Multi-select menu (arrow keys + space + enter). Returns list of indices.

    If allow_back is True, a "← Go back" option is appended. Selecting it (alone) returns [-1].
    Falls back to comma-separated input on non-interactive terminals.
    """
    effective = list(choices)
    if allow_back:
        effective.append(GO_BACK)

    if not sys.stdout.isatty():
        print(f"\n  {prompt}")
        for i, choice in enumerate(effective):
            print(f"    {i+1}. {choice}")
        while True:
            try:
                raw = input(f"  Enter numbers [1-{len(effective)}] (comma-separated): ")
                indices = [int(x.strip()) - 1 for x in raw.split(',') if x.strip()]
                if all(0 <= i < len(effective) for i in indices):
                    if allow_back and len(effective) - 1 in indices:
                        return [-1]
                    return [i for i in indices if i < len(choices)]
            except (ValueError, EOFError):
                pass

    result = questionary.checkbox(
        f'  {prompt}',
        choices=effective,
        style=QUESTIONARY_STYLE,
    ).ask()

    if result is None:
        return []
    if GO_BACK in result:
        return [-1]
    return [choices.index(r) for r in result]


def confirm(prompt: str, default: bool = True) -> bool:
    """Yes/No confirmation. Falls back to input on non-interactive terminals."""
    if not sys.stdout.isatty():
        suffix = '[Y/n]' if default else '[y/N]'
        ans = input(f'  {prompt} {suffix}: ').strip().lower()
        if default:
            return ans not in ('n', 'no')
        return ans in ('y', 'yes', '')

    return questionary.confirm(
        f'  {prompt}',
        default=default,
        style=QUESTIONARY_STYLE,
    ).ask()


def password(prompt: str) -> str:
    """Hidden password input."""
    if not sys.stdout.isatty():
        return input(f'  {prompt}: ').strip()

    result = questionary.password(
        f'  {prompt}',
        style=QUESTIONARY_STYLE,
    ).ask()
    return result or ''


def text_input(prompt: str, default: str = '') -> str:
    """Free-form text input."""
    if not sys.stdout.isatty():
        return input(f'  {prompt}: ').strip()

    result = questionary.text(
        f'  {prompt}',
        default=default,
        style=QUESTIONARY_STYLE,
    ).ask()
    return result or ''


def press_enter(prompt: str = 'Press Enter to continue...') -> None:
    """Wait for user to press Enter."""
    input(f'  {prompt} ')


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


def play_intro_rant(duration: float = 10.0) -> None:
    """Type out the intro rant character by character, then clear it.

    Falls back to a simple print on non-interactive terminals.
    """
    is_interactive = sys.stdout.isatty()

    if not is_interactive:
        console.print(dim=INTRO_RANT)
        time.sleep(2)
        return

    total_chars = len(INTRO_RANT)
    delay = duration / total_chars

    sys.stdout.write('\033[93m')  # bright yellow
    try:
        for ch in INTRO_RANT:
            sys.stdout.write(ch)
            sys.stdout.flush()
            if ch in '.?!':
                time.sleep(delay * 8)
            elif ch == ',':
                time.sleep(delay * 4)
            elif ch == '\n':
                time.sleep(delay * 6)
            else:
                time.sleep(delay)
    except KeyboardInterrupt:
        sys.stdout.write('\033[0m\n')
        return

    sys.stdout.write('\033[0m\n')
    sys.stdout.flush()
    time.sleep(5)

    # Clear the rant (count actual visual lines including wrapping)
    term_width = shutil.get_terminal_size().columns or 80
    visual_lines = 0
    for line in INTRO_RANT.split('\n'):
        visual_lines += max(1, -(-len(line) // term_width))  # ceil div
    visual_lines += 1  # extra line from final newline
    sys.stdout.write(f'\033[{visual_lines}F\033[J')
    sys.stdout.flush()


# ── Countdown ────────────────────────────────────────────────────────────

def countdown(seconds: int, message: str = 'Retrying') -> None:
    """Display a countdown. Uses rich for styled output."""
    for i in range(seconds, 0, -1):
        console.print(f'  [yellow]{message}[/yellow] in [cyan]{i}s[/cyan]...')
        time.sleep(1)
