"""Model PR state transitions."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Self
from zoneinfo import ZoneInfo

from github_client import (
    ClosedEvent,
    Event,
    Merge,
    PreviousPhaseApproved,
    Review,
    ReviewDismissed,
    ReviewRequested,
    ReviewRequestRemoved,
)


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


class PrStateMachine:
    """Models PR state transitions."""

    def __init__(self, create_time: datetime, last_approval: datetime | None):
        """Initialize."""
        self.last_event_time = (
            min(create_time, last_approval) if last_approval else create_time
        )
        self._last_state_change_time = self.last_event_time
        self.reviewer_states: dict[str, ReviewerState] = defaultdict(
            lambda: ReviewerState.NONE
        )
        self.total_under_review_duration: timedelta = timedelta(0)
        self.total_under_development_duration: timedelta = timedelta(0)
        self._state = PrState.WAITING if last_approval else PrState.UNDER_DEVELOPMENT
        self._previous_state = (
            PrState.WAITING if last_approval else PrState.UNDER_DEVELOPMENT
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

    def _update_pr_state(self, event: Event) -> timedelta | None:
        """Update the state in response to a new event."""
        if isinstance(event, Merge):
            new_state = PrState.MERGED
        elif isinstance(event, ClosedEvent):
            if self.state == PrState.MERGED:
                return None
            new_state = PrState.CLOSED
        # override new_state if currently waiting
        if (
            not self.reviewer_states["patrickkwang"] == ReviewerState.APPROVED
            and self.state == PrState.WAITING
        ):
            if isinstance(event, PreviousPhaseApproved):
                new_state = PrState.UNDER_DEVELOPMENT
            else:
                new_state = PrState.WAITING
        elapsed_in_state = event.created_at - self.last_state_change_time
        self.last_event_time = event.created_at

        if new_state is not None:
            pass
        elif self.reviewer_states["patrickkwang"] in (
            ReviewerState.APPROVED,
            ReviewerState.REVIEW_REQUESTED_POST_APPROVAL,
        ):
            new_state = PrState.APPROVED
            if self.finish_time is None:
                self.finish_time = self.last_review_requested
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

        if new_state == self.state:
            return None
        duration = event.created_at - self.last_state_change_time
        self._set_state(new_state, event.created_at)

        if self.previous_state == PrState.UNDER_REVIEW:
            self.total_under_review_duration += duration
        elif self.previous_state == PrState.UNDER_DEVELOPMENT:
            self.total_under_development_duration += duration
        return elapsed_in_state

    def _wrap_up(self):
        if self.state == PrState.UNDER_DEVELOPMENT:
            duration = now() - self.last_state_change_time
            self.total_under_development_duration += duration
        elif self.state == PrState.UNDER_REVIEW:
            duration = now() - self.last_state_change_time
            self.total_under_review_duration += duration

    def _update_reviewer_states(self: Self, event: Event):
        """Update reviewer states based on event."""
        if isinstance(event, (ReviewRequested, ReviewDismissed)):
            if self.reviewer_states[event.reviewer] != ReviewerState.APPROVED:
                self.reviewer_states[event.reviewer] = ReviewerState.REVIEW_REQUESTED
            else:
                self.reviewer_states[event.reviewer] = (
                    ReviewerState.REVIEW_REQUESTED_POST_APPROVAL
                )
            self.last_review_requested = event.created_at
        elif isinstance(event, ReviewRequestRemoved):
            del self.reviewer_states[event.reviewer]
        elif isinstance(event, Review) and event.state == "CHANGES_REQUESTED":
            self.reviewer_states[event.reviewer] = ReviewerState.REQUESTED_CHANGES
        elif isinstance(event, Review) and event.state == "DISMISSED":
            self.reviewer_states[event.reviewer] = ReviewerState.REVIEW_REQUESTED
        elif isinstance(event, Review) and (
            event.state == "APPROVED"
            or (event.state == "COMMENTED" and event.reviewer != "patrickkwang")
        ):
            # Both APPROVED and COMMENTED are considered approval.
            self.reviewer_states[event.reviewer] = ReviewerState.APPROVED

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
