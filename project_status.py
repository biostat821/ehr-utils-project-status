#!/usr/bin/env python
"""Utilities for analyzing and reporting EHR project status."""

import argparse
import csv
from itertools import accumulate
import math
import os
import re
import textwrap
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Self
from zoneinfo import ZoneInfo

import httpx
from pr_state_machine import PrState, PrStateMachine, ReviewerState


def guess_phase(pr_title: str) -> int | None:
    """Guess what phase a PR is associated with, based on the title."""
    pr_title = pr_title.lower()
    if "phase" in pr_title:
        # remove everything before the last occurrence of "phase"
        pr_title = pr_title.split("phase")[-1]
    numbers_pattern = r"((\d+)\D*)+"
    match = re.search(numbers_pattern, pr_title)
    if match is None:
        return None
    # return the first integer found
    return int(match.group(2))


def escape_latex(raw: str) -> str:
    """Escape ampersands in strings bound for LaTeX."""
    return raw.replace("&", "\\&")


def et_datetime(iso: str) -> datetime:
    """Parse ISO format as datetime in Eastern time."""
    return datetime.fromisoformat(iso).astimezone(ZoneInfo("America/New_York"))


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
        return "." * (padding - 1) + "\ " + x_str
    else:
        return "\ " * padding + x_str


def td_to_str(td: timedelta) -> str:
    """Convert timedelta to string."""
    sign = "-" if td < timedelta(0) else ""
    abs_td = abs(td)
    remainder = int(abs_td.total_seconds())
    days, remainder = divmod(remainder, 86400)
    string = ""
    if days == 1:
        string += f"{sign}{days} day, "
    elif days > 1:
        string += f"{sign}{days} days, "
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    string += f"{hours:02}:{minutes:02}:{seconds:02}"
    return string


@dataclass
class Event:
    created_at: datetime

    def get_summary(self: Self, verbose: bool = False) -> str:
        return f"{self.creation_time} & {type(self).__name__:20s}"

    @property
    def creation_time(self: Self) -> str:
        return self.created_at.strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class PullRequest:
    owner: str
    branch: str
    created_at: datetime
    title: str
    permalink: str
    number: int
    state: str
    based_on_main: bool
    behind_base: bool
    timeline_events: list[Event]


@dataclass
class ReviewRequested(Event):
    reviewer: str

    def get_summary(self: Self, verbose: bool = False) -> str:
        return f"{self.creation_time} & REVIEW_REQUESTED from {self.reviewer}"


@dataclass
class ReviewRequestRemoved(Event):
    reviewer: str

    def get_summary(self: Self, verbose: bool = False) -> str:
        return f"{self.creation_time} & REVIEW_REQUEST_REMOVED from {self.reviewer}"


@dataclass
class ReviewDismissed(Event):
    reviewer: str

    def get_summary(self: Self, verbose: bool = False) -> str:
        return f"{self.creation_time} & REVIEW_DISMISSED from {self.reviewer}"


@dataclass
class Review(Event):
    reviewer: str
    state: str

    def get_summary(self: Self, verbose: bool = False) -> str:
        return (
            f"{self.creation_time} & REVIEWED ({self.state.lower()}) by {self.reviewer}"
        )


@dataclass
class Merge(Event):
    def get_summary(self: Self, verbose: bool = False) -> str:
        return f"{self.creation_time} & MERGED"


@dataclass
class ClosedEvent(Event):
    def get_summary(self: Self, verbose: bool = False) -> str:
        return f"{self.creation_time} & CLOSED"


@dataclass
class Extension:
    name: str
    username: str
    phase: int
    due_date: datetime


def get_extensions(filename: str) -> list[Extension]:
    """Read extensions from file."""
    with open(filename) as f:
        csvreader = csv.DictReader(f)
        return [
            Extension(
                row["name"],
                row["username"],
                int(row["phase"]),
                et_datetime(row["due"]),
            )
            for row in csvreader
        ]


def get_phase_mapping_overrides(filename: str) -> dict[str, dict[int, list[int]]]:
    """Read phase mapping overrides from file."""
    with open(filename) as f:
        csvreader = csv.DictReader(f)
        phase_mapping_overrides: dict[str, dict[int, list[int]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for row in csvreader:
            phase_mapping_overrides[row["username"]][int(row["pr_number"])].append(
                int(row["phase"])
            )
    return phase_mapping_overrides


class EhrProjectStatus:
    """Multitool for managing EHR projects on GitHub."""

    def __init__(self: Self, organization: str):
        """Initialize."""
        self.organization = organization
        self.auth_token = os.getenv("GITHUB_TOKEN")
        self.headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.auth_token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        self.extensions = {
            (extension.username, extension.phase): extension
            for extension in get_extensions("extensions.csv")
        }
        self.phase_mapping_overrides = get_phase_mapping_overrides(
            "phase_mapping_overrides.csv"
        )

    def repo_name(self: Self, username: str):
        return f"ehr-utils-{username}"

    first_due_date = datetime(
        2025, 2, 12, 23, 59, 59, tzinfo=ZoneInfo("America/New_York")
    )
    inter_phase_durations = [
        timedelta(days=14),
        timedelta(days=21),
        timedelta(days=7),
        timedelta(days=14),
        timedelta(days=14),
    ]
    merge_due_dates: list[datetime] = list(
        accumulate(
            inter_phase_durations, lambda dt, td: dt + td, initial=first_due_date
        )
    )
    final_due_date = datetime(
        2025, 4, 25, 23, 59, 59, tzinfo=ZoneInfo("America/New_York")
    )

    def generate_pr_summary(
        self,
        pr: PullRequest,
        phase: int | None = None,
        last_approval: datetime | None = None,
        prior_adjusted_lateness: timedelta = timedelta(0),
    ) -> tuple[str, datetime | None, timedelta]:
        document = ""
        if phase is not None:
            extension = self.extensions.get((pr.owner, phase))
            rolling_due_date = (
                last_approval + self.inter_phase_durations[phase - 1]
                if last_approval and phase < 6
                else self.first_due_date
            )
            original_due_date = self.merge_due_dates[phase - 1]
            scheduled_due_date = extension.due_date if extension else original_due_date
            if rolling_due_date:
                due_date = min(
                    max(rolling_due_date, scheduled_due_date), self.final_due_date
                )
            else:
                due_date = scheduled_due_date
            document += "approval due"
            if due_date != original_due_date:
                document += (
                    f" \\sout{{{original_due_date.strftime('%Y-%m-%d %H:%M:%S')}}}"
                )
            document += f" {due_date.strftime('%Y-%m-%d %H:%M:%S')}"
            if extension:
                document += " (extension granted)\\\\\n"
        document += textwrap.dedent("""
                                    \\setlength\\LTleft{0pt}
                                    \\setlength\\LTright{0pt}
                                    \\begin{longtable}{@{\\extracolsep{\\fill}}llr}
                                    \\toprule
                                    \\textbf{timestamp} & \\textbf{event} & \\textbf{status} \\\\
                                    \\midrule
                                    """).strip()
        all_events = pr.timeline_events
        document += (
            f"\n{pr.created_at.strftime('%Y-%m-%d %H:%M:%S')} & CREATED & \\\\\n"
        )
        state = PrState.UNDER_DEVELOPMENT
        pr_state_machine = PrStateMachine(pr.created_at)
        approval = None
        for event in all_events:
            new_state = None
            if isinstance(event, (ReviewRequested, ReviewDismissed)):
                if (
                    pr_state_machine.reviewer_states[event.reviewer]
                    != ReviewerState.APPROVED
                ):
                    pr_state_machine.reviewer_states[event.reviewer] = (
                        ReviewerState.REVIEW_REQUESTED
                    )
                else:
                    pr_state_machine.reviewer_states[event.reviewer] = (
                        ReviewerState.REVIEW_REQUESTED_POST_APPROVAL
                    )
            elif isinstance(event, ReviewRequestRemoved):
                del pr_state_machine.reviewer_states[event.reviewer]
            elif isinstance(event, Review) and event.state == "CHANGES_REQUESTED":
                pr_state_machine.reviewer_states[event.reviewer] = (
                    ReviewerState.REQUESTED_CHANGES
                )
            elif isinstance(event, Review) and event.state == "DISMISSED":
                pr_state_machine.reviewer_states[event.reviewer] = (
                    ReviewerState.REVIEW_REQUESTED
                )
            elif isinstance(event, Review):  # both APPROVED and COMMENTED
                pr_state_machine.reviewer_states[event.reviewer] = (
                    ReviewerState.APPROVED
                )
            elif isinstance(event, Merge):
                new_state = PrState.MERGED
            elif isinstance(event, ClosedEvent):
                if pr_state_machine.state == PrState.MERGED:
                    continue
                new_state = PrState.CLOSED
            previous_state, state, elapsed, elapsed_in_state = (
                pr_state_machine.update_state(event.created_at, new_state)
            )
            if state == PrState.APPROVED and approval is None:
                approval = event.created_at
            if elapsed_in_state is not None:
                document += f"{event.get_summary()} & {previous_state.value} for {pad_to(td_to_str(elapsed_in_state), 17)} \\\\\n"
            else:
                document += f"{event.get_summary()} & \\\\\n"
        if pr_state_machine.state == PrState.UNDER_DEVELOPMENT:
            duration = now() - pr_state_machine.last_state_change_time
            pr_state_machine.total_under_development_duration += duration
        elif pr_state_machine.state == PrState.UNDER_REVIEW:
            duration = now() - pr_state_machine.last_state_change_time
            pr_state_machine.total_under_review_duration += duration
        out_of_slo = pr_state_machine.out_of_slo_under_review_duration
        finish_time = (
            pr_state_machine.finish_time
            if pr_state_machine.finish_time is not None
            else now()
        )

        document += "\midrule\n"
        document += f"&& under development for {pad_to(td_to_str(pr_state_machine.total_under_development_duration), 17)} \\\\\n"
        document += f"&& under review for {pad_to(td_to_str(pr_state_machine.total_under_review_duration), 17)} \\\\\n"
        document += (
            f"&& reviews out of SLO for {pad_to(td_to_str(out_of_slo), 17)} \\\\\n"
        )
        if due_date and approval:
            late_by = max(finish_time - due_date, timedelta(0))
            adjusted_lateness = max(late_by - out_of_slo, timedelta(0))
            document += f"&& late by {pad_to(td_to_str(late_by), 17)} \\\\\n"
        else:
            adjusted_lateness = -out_of_slo
        document += (
            f"&& adjusted lateness: {pad_to(td_to_str(adjusted_lateness), 17)} \\\\\n"
        )
        document += "\midrule\n"
        cumulative_adjusted_lateness = adjusted_lateness + prior_adjusted_lateness
        document += f"&& cumulative adjusted lateness: {pad_to(td_to_str(cumulative_adjusted_lateness), 17)} \\\\\n"
        if approval:
            points_deducted = max(
                math.ceil(cumulative_adjusted_lateness / timedelta(days=1)), 0
            )
            document += f"&& \\textbf{{points deducted}}: \\textbf{{{pad_to(points_deducted, 17)}}} \\\\\n"
        document += textwrap.dedent("""
                                    \\bottomrule
                                    \end{longtable}
                                    """).strip()
        return document, approval, adjusted_lateness

    def infer_phases(self, pr: PullRequest, idx: int) -> list[int]:
        """Infer which phase(s) this PR is for.

        idx indicates where it falls in creation order (zero-indexed).
        """
        if (
            pr.owner in self.phase_mapping_overrides
            and pr.number in self.phase_mapping_overrides[pr.owner]
        ):
            return self.phase_mapping_overrides[pr.owner][pr.number]
        if pr.owner in self.phase_mapping_overrides and idx + 1 in [
            phase
            for phases in self.phase_mapping_overrides[pr.owner].values()
            for phase in phases
        ]:
            return []
        return [idx + 1]

    def generate_pr_summaries(self: Self, username: str) -> None:
        """Generate PR summaries."""
        prs = [pr for pr in self.list_prs(username) if pr.based_on_main]
        not_closed_prs = [pr for pr in prs if pr.state != "CLOSED"]
        closed_prs = [pr for pr in prs if pr.state == "CLOSED"]
        if len(not_closed_prs) > len(self.merge_due_dates):
            raise ValueError("Too many PRs!")
        document = textwrap.dedent(f"""
                    \\documentclass{{article}}
                    \\usepackage[includehead, includefoot, portrait, margin=0.5in]{{geometry}}
                    \\usepackage{{booktabs}}
                    \\usepackage[colorlinks=true, urlcolor=blue]{{hyperref}}
                    \\usepackage{{longtable}}
                    \\usepackage{{fancyhdr}}               
                    \\usepackage{{lmodern}}
                    \\usepackage[normalem]{{ulem}}
                    \\newcommand{{\\setfont}}{{
                        \\ttfamily\\fontseries{{l}}\\selectfont\\small
                    }}
                    \\begin{{document}}
                    \\pagestyle{{fancy}}
                    \\fancyhead{{}} \\fancyfoot{{}}
                    \\fancyhead[L]{{\\setfont {now().strftime("%Y-%m-%d %H:%M:%S")}}}
                    \\fancyhead[C]{{\\setfont {username}}}
                    \\fancyhead[R]{{\\setfont \\href{{https://github.com/biostat821/ehr-utils-project-status/tree/v0.7.0}}{{ehr-utils-project-status 0.7.0}}}}
                    \\ttfamily
                    \\fontseries{{l}}\\selectfont
                    \\small""").strip()
        not_closed_pr_phases = [
            (pr, self.infer_phases(pr, idx)) for idx, pr in enumerate(not_closed_prs)
        ]
        closed_pr_phases = [
            (pr, guess_phase(pr.title)) for idx, pr in enumerate(closed_prs)
        ]
        phase_prs = defaultdict(list)
        for pr, phase in closed_pr_phases:
            if phase:
                phase_prs[phase].append(pr)
        for pr, phases in not_closed_pr_phases:
            for phase in phases:
                phase_prs[phase].append(pr)
        summaries = []

        last_approval = None
        for phase, prs in sorted(phase_prs.items()):
            cumulative_adjusted_lateness = timedelta(0)
            for pr in prs:
                summary, approval, adjusted_lateness = self.generate_pr_summary(
                    pr, phase, last_approval, cumulative_adjusted_lateness
                )
                cumulative_adjusted_lateness += adjusted_lateness
                if approval and phase < len(self.merge_due_dates):
                    last_approval = approval
                summaries.append((phase, pr, summary))
        pages = [
            f"\\fancyfoot[R]{{\\setfont phase {phase:02}}}"
            + "\n\\noindent\n\\textbf{pull request}:\\\\\n"
            + f'"{escape_latex(pr.title)}" (branch "{pr.branch}")\\\\\n'
            + f"\\url{{{pr.permalink}}}\\\\\n"
            + (
                "\\\\\n"
                + "\\textbf{inferred phase}:\\\\\n"
                + f"{phase:02} (\\url{{https://github.com/biostat821/ehr-utils-project/blob/main/phase{phase:02}.md}})\\\\\n"
                if phase in {1, 2, 3, 4, 5, 6}
                else ""
            )
            + summary
            for phase, pr, summary in summaries
        ]
        document += "\n\\pagebreak\n".join(pages)
        document += "\n\\end{document}"
        document = document.replace("_", "\\_")
        with open(f"outputs/{username}.tex", "w") as f:
            f.write(document)

    def parse_pr(self, pr):
        timeline_items = [edge["node"] for edge in pr["timelineItems"]["edges"]]
        reviews_requested = [
            ReviewRequested(
                created_at=et_datetime(timeline_item["createdAt"]),
                reviewer=timeline_item["requestedReviewer"]["login"],
            )
            for timeline_item in timeline_items
            if timeline_item["__typename"] == "ReviewRequestedEvent"
            and "login"
            in timeline_item[
                "requestedReviewer"
            ]  # there is no "login" if the reviewer is Copilot
        ]
        reviews_dismissed = [
            ReviewDismissed(
                created_at=et_datetime(timeline_item["createdAt"]),
                reviewer=timeline_item["review"]["author"]["login"],
            )
            for timeline_item in timeline_items
            if timeline_item["__typename"] == "ReviewDismissedEvent"
        ]
        review_requests_removed = [
            ReviewRequestRemoved(
                created_at=et_datetime(timeline_item["createdAt"]),
                reviewer=timeline_item["requestedReviewer"]["login"],
            )
            for timeline_item in timeline_items
            if timeline_item["__typename"] == "ReviewRequestRemovedEvent"
        ]
        reviews = [
            Review(
                created_at=et_datetime(timeline_item["createdAt"]),
                reviewer=timeline_item["author"]["login"],
                state=timeline_item["state"],
            )
            for timeline_item in timeline_items
            if timeline_item["__typename"] == "PullRequestReview"
        ]
        merges = [
            Merge(
                created_at=et_datetime(timeline_item["createdAt"]),
            )
            for timeline_item in timeline_items
            if timeline_item["__typename"] == "MergedEvent"
        ]
        closes = [
            ClosedEvent(
                created_at=et_datetime(timeline_item["createdAt"]),
            )
            for timeline_item in timeline_items
            if timeline_item["__typename"] == "ClosedEvent"
        ]

        return sorted(
            reviews_requested
            + reviews
            + review_requests_removed
            + reviews_dismissed
            + merges
            + closes,
            key=lambda event: event.created_at,
        )

    def list_prs(self: Self, username: str) -> list[PullRequest]:
        """Get data for non-closed PRs."""
        response = httpx.post(
            "https://api.github.com/graphql",
            headers=self.headers,
            json={
                "query": f"""
                {{
                    repository(owner: "{self.organization}", name: "{self.repo_name(username)}") {{
                        defaultBranchRef {{
                            target {{
                                ... on Commit {{
                                    id
                                }}
                            }}
                        }}
                        pullRequests(first: 100, states:[CLOSED, OPEN, MERGED]) {{
                            edges {{
                                node {{
                                    createdAt
                                    number
                                    state
                                    permalink
                                    title
                                    baseRef {{
                                        target {{
                                            ... on Commit {{
                                                id
                                            }}
                                        }}
                                    }}
                                    headRefName
                                    commits(first: 1) {{
                                        nodes {{
                                            commit {{
                                                history(first: 100) {{
                                                    nodes {{
                                                        id
                                                    }}
                                                }}
                                            }}
                                        }}
                                    }}
                                    timelineItems(last: 100) {{
                                        edges {{
                                            node {{
                                                __typename
                                                ... on PullRequestReview {{
                                                    createdAt
                                                    author {{
                                                        login
                                                    }}
                                                    body
                                                    state
                                                }}
                                                ... on ReviewRequestedEvent {{
                                                    createdAt
                                                    requestedReviewer {{
                                                    ... on User {{
                                                        login
                                                    }}
                                                    }}
                                                }}
                                                ... on ReviewRequestRemovedEvent {{
                                                    createdAt
                                                    requestedReviewer {{
                                                    ... on User {{
                                                        login
                                                    }}
                                                    }}
                                                }}
                                                ... on ReviewDismissedEvent {{
                                                    createdAt
                                                    review {{
                                                    author {{
                                                        login
                                                    }}
                                                    }}
                                                }}
                                                ... on MergedEvent {{
                                                    createdAt
                                                }}
                                                ... on ClosedEvent {{
                                                    createdAt
                                                }}
                                            }}
                                        }}
                                    }}
                                }}
                            }}
                        }}
                    }}
                }}
                """
            },
        )
        main_id = response.json()["data"]["repository"]["defaultBranchRef"]["target"][
            "id"
        ]
        prs = [
            edge["node"]
            for edge in response.json()["data"]["repository"]["pullRequests"]["edges"]
        ]
        prs = [
            {
                "created_at": et_datetime(pr["createdAt"]),
                "number": pr["number"],
                "state": pr["state"],
                "title": pr["title"],
                "permalink": pr["permalink"],
                # it seems like baseRef can be None if the base branch has been deleted
                "base_id": pr["baseRef"]["target"]["id"] if pr["baseRef"] else None,
                "branch": pr["headRefName"],
                "commit_ids": [
                    node["id"]
                    for node in pr["commits"]["nodes"][0]["commit"]["history"]["nodes"]
                ]
                if pr["commits"]["nodes"]  # there may be no commits
                else [],
                "timeline_events": self.parse_pr(pr),
            }
            for pr in prs
        ]

        return sorted(
            [
                PullRequest(
                    username,
                    pr["branch"],
                    pr["created_at"],
                    pr["title"],
                    pr["permalink"],
                    pr["number"],
                    pr["state"],
                    based_on_main=pr["base_id"] == main_id,
                    behind_base=pr["base_id"] not in pr["commit_ids"],
                    timeline_events=pr["timeline_events"],
                )
                for pr in prs
            ],
            key=lambda pr: pr.created_at,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog="ProjectStatus",
        description="Generates project status reports",
    )
    parser.add_argument("username")
    args = parser.parse_args()
    EhrProjectStatus("biostat821-2025").generate_pr_summaries(args.username.strip())
