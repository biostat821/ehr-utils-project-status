"""Model PR state transitions."""

from collections import defaultdict
from datetime import datetime, timedelta
from enum import Enum


class ReviewerState(Enum):
    """State of a reviewer."""

    NONE = "none"
    REVIEW_REQUESTED = "review requested"
    REQUESTED_CHANGES = "requested changes"
    APPROVED = "approved"
    REVIEW_REQUESTED_POST_APPROVAL = "review requested (post-approval)"


class PrState(Enum):
    """State of a PR."""

    UNDER_DEVELOPMENT = "under development"
    UNDER_REVIEW = "under review"
    APPROVED = "approved"
    MERGED = "merged"
    CLOSED = "closed"


class PrStateMachine:
    """Models PR state transitions."""

    def __init__(self, start_time: datetime):
        """Initialize."""
        self.last_event_time = start_time
        self._last_state_change_time = start_time
        self.reviewer_states: dict[str, ReviewerState] = defaultdict(
            lambda: ReviewerState.NONE
        )
        self.total_under_review_duration: timedelta = timedelta(0)
        self.out_of_slo_under_review_duration: timedelta = timedelta(0)
        self.total_under_development_duration: timedelta = timedelta(0)
        self._state = PrState.UNDER_DEVELOPMENT
        self._previous_state = PrState.UNDER_DEVELOPMENT
        self.finish_time: datetime | None = None

    @property
    def state(self) -> PrState:
        return self._state

    @property
    def last_state_change_time(self) -> datetime:
        return self._last_state_change_time

    def set_state(self, state: PrState, event_time: datetime):
        self._last_state_change_time = event_time
        self._previous_state = self._state
        self._state = state

    @property
    def previous_state(self) -> PrState:
        return self._previous_state

    def maybe_change_state(self, state: PrState, event_time: datetime):
        """Change states if the new state is different from the old one."""
        if state == self.state:
            return None
        duration = event_time - self.last_state_change_time
        self.set_state(state, event_time)
        return duration

    def update_state(
        self, event_time: datetime, state: PrState | None = None
    ) -> tuple[PrState, PrState, timedelta, timedelta | None]:
        """Update the state in response to a new event."""
        elapsed = event_time - self.last_event_time
        elapsed_in_state = event_time - self.last_state_change_time
        self.last_event_time = event_time

        if state is not None:
            pass
        elif self.reviewer_states["patrickkwang"] in (
            ReviewerState.APPROVED,
            ReviewerState.REVIEW_REQUESTED_POST_APPROVAL,
        ):
            state = PrState.APPROVED
            if self.finish_time is None:
                self.finish_time = event_time
        elif any(
            reviewer_state == ReviewerState.REQUESTED_CHANGES
            for reviewer_state in self.reviewer_states.values()
        ):
            state = PrState.UNDER_DEVELOPMENT
        elif any(
            reviewer_state == ReviewerState.REVIEW_REQUESTED
            for reviewer_state in self.reviewer_states.values()
        ):
            state = PrState.UNDER_REVIEW
        else:
            state = PrState.UNDER_DEVELOPMENT

        duration = self.maybe_change_state(state, event_time)
        if duration is None:
            return self.state, self.state, elapsed, None

        if self.previous_state == PrState.UNDER_REVIEW:
            self.total_under_review_duration += duration
            self.out_of_slo_under_review_duration += max(
                timedelta(0), duration - timedelta(days=3)
            )
        elif self.previous_state == PrState.UNDER_DEVELOPMENT:
            self.total_under_development_duration += duration
        return self.previous_state, self.state, elapsed, elapsed_in_state
