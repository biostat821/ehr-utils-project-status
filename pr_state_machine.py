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


class PrStateMachine:
    """Models PR state transitions."""

    def __init__(self, start_time: datetime, due_time: datetime):
        """Initialize."""
        self.last_event_time = start_time
        self.last_state_change_time = start_time
        self.due_time = due_time
        self.reviewer_states: dict[str, ReviewerState] = defaultdict(
            lambda: ReviewerState.NONE
        )
        self.total_under_review_duration: timedelta = timedelta(0)
        self.out_of_slo_under_review_duration: timedelta = timedelta(0)
        self.total_under_development_duration: timedelta = timedelta(0)
        self.state = PrState.UNDER_DEVELOPMENT
        self.finish_time: datetime | None = None

    def maybe_change_state(self, state: PrState, event_time: datetime):
        """Change states if the new state is different from the old one."""
        if state == self.state:
            return None
        duration = event_time - self.last_state_change_time
        self.last_state_change_time = event_time
        previous_state = self.state
        self.state = state
        return previous_state, duration

    def update_state(
        self, event_time: datetime
    ) -> tuple[PrState, timedelta, timedelta | None]:
        """Update the state in response to a new event."""
        elapsed = event_time - self.last_event_time
        elapsed_in_state = event_time - self.last_state_change_time
        self.last_event_time = event_time

        if self.state == PrState.MERGED:
            return PrState.APPROVED, elapsed, elapsed_in_state
        if self.reviewer_states["patrickkwang"] in (
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

        state_change = self.maybe_change_state(state, event_time)
        if state_change is None:
            return self.state, elapsed, None
        previous_state, duration = state_change

        if previous_state == PrState.UNDER_REVIEW:
            self.total_under_review_duration += duration
            self.out_of_slo_under_review_duration += max(
                timedelta(0), duration - timedelta(days=3)
            )
        elif previous_state == PrState.UNDER_DEVELOPMENT:
            self.total_under_development_duration += duration
        return previous_state, elapsed, elapsed_in_state
