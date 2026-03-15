from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from zoneinfo import ZoneInfo

NUM_PHASES = 6
PHASES = set(range(1, NUM_PHASES + 1))


class PrState(Enum):
    """State of a PR."""

    WAITING = "awaiting previous phase"
    UNDER_DEVELOPMENT = "under development"
    UNDER_REVIEW = "under review"
    APPROVED = "approved"
    MERGED = "merged"
    CLOSED = "closed"


@dataclass
class Entry:
    summary: str
    previous_state: PrState
    elapsed_in_state: timedelta | None


@dataclass
class DocumentSpec:
    entries: list[Entry]
    total_under_development_duration: timedelta
    total_under_review_duration: timedelta
    late_by: timedelta | None
    points_deducted: int | None


def now() -> datetime:
    """Get current date time in Eastern time zone."""
    return datetime.now(tz=ZoneInfo("America/New_York")).replace(microsecond=0)


def pad_to(x, n: int) -> str:
    """Convert to string and pad with escaped spaces.

    This is handy for LaTeX with monospaced font.
    """
    x_str = str(x)
    padding = n - len(x_str)
    if padding >= 3:
        return "." * (padding - 1) + r"\ " + x_str
    else:
        return r"\ " * padding + x_str


def td_to_str(td: timedelta) -> str:
    """Convert timedelta to string."""
    abs_td = abs(td)
    remainder = int(abs_td.total_seconds())
    days, remainder = divmod(remainder, 86400)
    string = "-" if td < timedelta(0) else ""
    if days == 1:
        string += f"{days} day, "
    elif days > 1:
        string += f"{days} days, "
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    string += f"{hours:02}:{minutes:02}:{seconds:02}"
    return string


def str_to_td(string: str) -> timedelta:
    """Convert string to timedelta."""
    parts = string.split(",")
    if len(parts) == 2:
        days_str = parts[0].strip().split()[0]
        days = int(days_str)
        time_str = parts[1].strip()
    else:
        days = 0
        time_str = string

    h, m, s = map(float, time_str.split(":"))
    return timedelta(days=days, hours=h, minutes=m, seconds=s)
