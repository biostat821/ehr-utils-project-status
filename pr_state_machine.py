"""Model PR state transitions."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Self
from zoneinfo import ZoneInfo

from github_client import Event
from project_util import Entry, PrState


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


_PAUSES = {
    "spring break": Period(
        datetime(2026, 3, 6, hour=19, tzinfo=ZoneInfo("America/New_York")),
        datetime(2026, 3, 16, hour=8, minute=30, tzinfo=ZoneInfo("America/New_York")),
    )
}

_APPROVERS = {"patrickkwang", "Surguladze99", "skylershapiro"}


def now() -> datetime:
    """Get current date time in Eastern time zone."""
    return datetime.now(tz=ZoneInfo("America/New_York")).replace(microsecond=0)


class ReviewerState(Enum):
    """State of a reviewer."""

    NONE = "none"
    REVIEW_REQUESTED = "review requested"
    REQUESTED_CHANGES = "requested changes"
    APPROVED = "approved"
    REVIEW_REQUESTED_POST_APPROVAL = "review requested (post-approval)"


class PrStateMachine:
    """Models PR state transitions."""

    def __init__(
        self, create_time: datetime, phase_start_time: datetime, should_wait: bool
    ):
        """Initialize."""
        self._last_state_change_time = (
            min(create_time, phase_start_time) if phase_start_time else create_time
        )
        self.reviewer_states: dict[str, ReviewerState] = defaultdict(
            lambda: ReviewerState.NONE
        )
        self.total_under_review_duration: timedelta = timedelta(0)
        self.total_under_development_duration: timedelta = timedelta(0)
        self._state = PrState.WAITING if should_wait else PrState.UNDER_DEVELOPMENT
        self._previous_state = (
            PrState.WAITING if should_wait else PrState.UNDER_DEVELOPMENT
        )
        self.last_review_requested: datetime | None = None
        self.finish_time: datetime | None = None

    @property
    def state(self) -> PrState:
        return self._state

    @property
    def last_state_change_time(self) -> datetime:
        return self._last_state_change_time

    def _set_state(self, state: PrState, event_time: datetime):
        self._last_state_change_time = event_time
        self._previous_state = self._state
        self._state = state

    @property
    def previous_state(self) -> PrState:
        return self._previous_state

    @property
    def approved(self) -> bool:
        for approver in _APPROVERS:
            if self.reviewer_states[approver] in (
                ReviewerState.APPROVED,
                ReviewerState.REVIEW_REQUESTED_POST_APPROVAL,
            ):
                return True
        return False

    def _update_pr_state_based_on_reviewers(self, event: Event) -> PrState:
        if self.approved:
            new_state = PrState.APPROVED
            if self.finish_time is None:
                self.finish_time = self.last_review_requested
        elif self.state == PrState.WAITING:
            if event.type == "PREVIOUS_PHASE_APPROVED":
                new_state = PrState.UNDER_DEVELOPMENT
            else:
                new_state = PrState.WAITING
        elif any(
            reviewer_state == ReviewerState.REQUESTED_CHANGES
            for reviewer_state in self.reviewer_states.values()
        ):
            new_state = PrState.UNDER_DEVELOPMENT
        elif any(
            reviewer_state == ReviewerState.REVIEW_REQUESTED
            for reviewer_state in self.reviewer_states.values()
        ):
            new_state = PrState.UNDER_REVIEW
        else:
            new_state = PrState.UNDER_DEVELOPMENT
        return new_state

    def _update_pr_state(self, event: Event) -> timedelta | None:
        """Update the state in response to a new event."""
        if self.state == PrState.CLOSED and event.type != "REOPENED":
            return None
        elif event.type == "CLOSED":
            if self.state == PrState.MERGED:
                new_state = PrState.MERGED
            else:
                new_state = PrState.CLOSED
        elif event.type == "MERGED":
            new_state = PrState.MERGED
        else:
            new_state = self._update_pr_state_based_on_reviewers(event)

        if new_state == self.state:
            return None
        in_state_period = Period(self.last_state_change_time, event.created_at)
        elapsed_in_state = in_state_period.duration
        self._set_state(new_state, event.created_at)

        if self.previous_state == PrState.UNDER_REVIEW:
            self.total_under_review_duration += elapsed_in_state
        elif self.previous_state == PrState.UNDER_DEVELOPMENT:
            self.total_under_development_duration += (
                in_state_period - _PAUSES["spring break"]
            )

        return elapsed_in_state

    def _wrap_up(self):
        in_state_period = Period(self.last_state_change_time, now())
        if self.state == PrState.UNDER_DEVELOPMENT:
            self.total_under_development_duration += (
                in_state_period - _PAUSES["spring break"]
            )
        elif self.state == PrState.UNDER_REVIEW:
            self.total_under_review_duration += in_state_period.duration

    def _update_reviewer_states(self: Self, event: Event):
        """Update reviewer states based on event."""
        reviewer = event.reviewer
        if reviewer is None:
            return
        if event.type in ("REVIEW_REQUESTED", "REVIEW_DISMISSED"):
            if self.reviewer_states[reviewer] != ReviewerState.APPROVED:
                self.reviewer_states[reviewer] = ReviewerState.REVIEW_REQUESTED
            else:
                self.reviewer_states[reviewer] = (
                    ReviewerState.REVIEW_REQUESTED_POST_APPROVAL
                )
            self.last_review_requested = event.created_at
        elif event.type == "REVIEW_REQUEST_REMOVED":
            del self.reviewer_states[reviewer]
        elif event.type == "CHANGES_REQUESTED":
            self.reviewer_states[reviewer] = ReviewerState.REQUESTED_CHANGES
        elif event.type == "APPROVED":
            self.reviewer_states[reviewer] = ReviewerState.APPROVED
        # ignore COMMENTED

    def process_events(
        self: Self, events: list[Event]
    ) -> tuple[list[Entry], datetime | None]:
        """Process events."""
        approval = None
        entries = []
        for event in events:
            self._update_reviewer_states(event)
            elapsed_in_state = self._update_pr_state(event)
            if self.state == PrState.APPROVED and approval is None:
                approval = event.created_at
            entries.append(
                Entry(event.get_summary(), self.previous_state, elapsed_in_state)
            )
        self._wrap_up()
        return entries, approval
