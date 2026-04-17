from __future__ import annotations

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
    timestamp: str
    summary: str
    previous_state: PrState
    elapsed_in_state: timedelta | None


@dataclass
class Period:
    start: datetime
    end: datetime

    @property
    def duration(self) -> timedelta:
        return self.end - self.start

    def __str__(self) -> str:
        return f"[{self.start} -> {self.end})"

    def __sub__(self, other: Period) -> timedelta:
        """Return time in this period but not in the other period."""
        if other.start > self.end or self.start > other.end:
            return self.end - self.start
        duration = timedelta()
        if self.start < other.start:
            duration += other.start - self.start
        if other.end < self.end:
            duration += self.end - other.end
        return duration


@dataclass
class DocumentSpec:
    entries: list[Entry]
    total_under_development_duration: timedelta
    total_under_review_duration: timedelta
    late_by: timedelta | None
    points_deducted: int | None
    extensions: dict[int, timedelta]


def now() -> datetime:
    """Get current date time in Eastern time zone."""
    return datetime.now(tz=ZoneInfo("America/New_York")).replace(microsecond=0)


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
