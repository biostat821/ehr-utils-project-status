#!/usr/bin/env python
"""Utilities for analyzing and reporting EHR project status."""

import argparse
import csv
import math
import re
import textwrap
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Self
from zoneinfo import ZoneInfo

from github_client import (
    Created,
    GithubClient,
    PreviousPhaseApproved,
    PullRequest,
)
from pr_state_machine import Entry, PrStateMachine, PrState

NUM_PHASES = 6
PHASES = set(range(1, NUM_PHASES + 1))


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
    guess = int(match.group(2))
    return guess if guess in PHASES else None


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


def create_page_header(phase: int, pr: PullRequest) -> str:
    return (
        f"\\fancyfoot[R]{{\\setfont phase {phase:02}}}"
        + "\n\\noindent\n\\textbf{pull request}:\\\\\n"
        + f'"{escape_latex(pr.title)}" (branch "{pr.branch}")\\\\\n'
        + f"\\url{{{pr.permalink}}}\\\\\n"
        + (
            "\\\\\n"
            + "\\textbf{inferred phase}:\\\\\n"
            + f"{phase:02} (\\url{{https://github.com/biostat821/ehr-utils-project/blob/main/phase{phase:02}.md}})\\\\\n"
            if phase in PHASES
            else ""
        )
    )


def write_document(username: str, pr_reports: list[tuple[int, PullRequest, str]]):
    pages = [
        create_page_header(phase, pr) + pr_report for phase, pr, pr_report in pr_reports
    ]
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
                \\fancyhead[R]{{\\setfont \\href{{https://github.com/biostat821/ehr-utils-project-status/tree/v1.0.0}}{{ehr-utils-project-status 1.0.0}}}}
                \\ttfamily
                \\fontseries{{l}}\\selectfont
                \\small""").strip()
    if not pages:
        document += "\nNo pull requests"
    document += "\n\\pagebreak\n".join(pages)
    document += "\n\\end{document}"
    document = document.replace("_", "\\_")
    with open(f"outputs/{username}.tex", "w") as f:
        f.write(document)


@dataclass
class DueDate:
    due_date: datetime
    original_due_date: datetime
    extension: Extension | None


@dataclass
class DocumentSpec:
    entries: list[Entry]
    total_under_development_duration: timedelta
    total_under_review_duration: timedelta
    late_by: timedelta | None
    points_deducted: int | None


def _construct_pr_report(documentSpec: DocumentSpec) -> str:
    document = ""
    document += textwrap.dedent("""
                                \\setlength\\LTleft{0pt}
                                \\setlength\\LTright{0pt}
                                \\begin{longtable}{@{\\extracolsep{\\fill}}llr}
                                \\toprule
                                \\textbf{timestamp} & \\textbf{event} & \\textbf{status} \\\\
                                \\midrule
                                """).strip()
    for entry in documentSpec.entries:
        event_summary = entry.summary
        previous_state = entry.previous_state
        elapsed_in_state = entry.elapsed_in_state
        if elapsed_in_state:
            document += f"{event_summary} & {previous_state.value} for {pad_to(td_to_str(elapsed_in_state), 17)} \\\\\n"
        else:
            document += f"{event_summary} & \\\\\n"
    document += "\midrule\n"
    document += f"&& under development for {pad_to(td_to_str(documentSpec.total_under_development_duration), 17)} \\\\\n"
    document += f"&& under review for {pad_to(td_to_str(documentSpec.total_under_review_duration), 17)} \\\\\n"
    if documentSpec.late_by:
        document += f"&& late by {pad_to(td_to_str(documentSpec.late_by), 17)} \\\\\n"
    if documentSpec.points_deducted is not None:
        document += f"&& \\textbf{{points deducted}}: \\textbf{{{pad_to(documentSpec.points_deducted, 17)}}} \\\\\n"
    document += textwrap.dedent("""
                                \\bottomrule
                                \end{longtable}
                                """).strip()
    return document


class EhrProjectStatus:
    """Multitool for managing EHR projects on GitHub."""

    def __init__(self: Self, organization: str, username: str, name: str):
        """Initialize."""
        self.username = username
        self.name = name
        self.extensions = {
            (extension.username, extension.phase): extension
            for extension in get_extensions("extensions.csv")
        }
        self.phase_mapping_overrides = get_phase_mapping_overrides(
            "phase_mapping_overrides.csv"
        )
        self.github_client = GithubClient(organization, username)

    start_time = datetime(2026, 2, 13, 23, 59, 59, tzinfo=ZoneInfo("America/New_York"))
    phase_time_budget = timedelta(days=7)

    def _generate_pr_report(
        self,
        pr: PullRequest,
        phase: int | None = None,
        last_approval: datetime | None = None,
    ) -> tuple[str, datetime | None, timedelta]:
        """Generate PR summary."""
        phase_start_time = last_approval if last_approval else self.start_time
        all_events = sorted(
            [Created(pr.created_at)]
            + pr.timeline_events
            + ([PreviousPhaseApproved(last_approval)] if last_approval else []),
            key=lambda event: event.created_at,
        )
        pr_state_machine = PrStateMachine(
            pr.created_at, phase_start_time, should_wait=last_approval is not None
        )
        entries, approval = pr_state_machine.process_events(all_events)

        late_by = (
            pr_state_machine.total_under_development_duration - self.phase_time_budget
        )
        if pr_state_machine.finish_time:
            points_deducted = max(math.ceil(late_by / timedelta(days=1)), 0)
        else:
            points_deducted = None

        pr_report = _construct_pr_report(
            DocumentSpec(
                entries,
                pr_state_machine.total_under_development_duration,
                pr_state_machine.total_under_review_duration,
                late_by,
                points_deducted,
            ),
        )
        if points_deducted is not None:
            with open("outputs/_summary.csv", "a") as f:
                f.write(
                    f'"{self.name}",{self.username},{phase},{pr.permalink},{100 - points_deducted}\n'
                )
        with open("outputs/_state_summary.csv", "a") as f:
            waiting_for = None
            if (
                pr_state_machine.last_review_requested
                and pr_state_machine.state == PrState.UNDER_REVIEW
            ):
                waiting_for = now() - pr_state_machine.last_review_requested
            f.write(
                f'"{self.name}",{self.username},{phase},{pr.permalink},{pr_state_machine.state},{late_by},{waiting_for}\n'
            )
        return pr_report, approval, late_by

    def _infer_phases(self, pr: PullRequest, next_phase: int) -> list[int]:
        """Infer which phase(s) this PR is for.

        next_phases indicates the next unclaimed phase.
        """
        if (
            pr.owner in self.phase_mapping_overrides
            and pr.number in self.phase_mapping_overrides[pr.owner]
        ):
            return self.phase_mapping_overrides[pr.owner][pr.number]
        # abort if next_phase is already claimed by an override
        if pr.owner in self.phase_mapping_overrides and next_phase in [
            phase
            for phases in self.phase_mapping_overrides[pr.owner].values()
            for phase in phases
        ]:
            return []
        return [next_phase]

    def _get_phase_prs(self: Self) -> dict[int, list[PullRequest]]:
        prs = [pr for pr in self.github_client.list_prs() if pr.based_on_main]
        not_closed_prs = [pr for pr in prs if pr.state != "CLOSED"]
        closed_prs = [pr for pr in prs if pr.state == "CLOSED"]
        if len(not_closed_prs) > NUM_PHASES:
            raise ValueError("Too many open/merged PRs!")
        closed_pr_phases = [
            (pr, guess_phase(pr.title)) for idx, pr in enumerate(closed_prs)
        ]
        not_closed_pr_phases = []
        max_phase = 0
        for pr in not_closed_prs:
            if max_phase + 1 not in PHASES:
                # Too many not-closed PRs! Treat the remainder as closed.
                closed_pr_phases.append((pr, guess_phase(pr.title)))
                continue
            phases = self._infer_phases(pr, max_phase + 1)
            if phases:
                max_phase = max(phases + [max_phase])
            not_closed_pr_phases.append((pr, phases))
        phase_prs = defaultdict(list)
        for pr, phase in closed_pr_phases:
            if phase:
                phase_prs[phase].append(pr)
        for pr, phases in not_closed_pr_phases:
            for phase in phases:
                phase_prs[phase].append(pr)
        return phase_prs

    def generate_project_report(self: Self) -> None:
        """Generate PR summaries."""
        phase_prs = self._get_phase_prs()
        pr_reports = []

        last_approval = None
        for phase, prs in sorted(phase_prs.items()):
            for pr in prs:
                pr_report, approval, _ = self._generate_pr_report(
                    pr, phase, last_approval
                )
                pr_reports.append((phase, pr, pr_report))
            if approval and phase < NUM_PHASES:
                last_approval = approval
            else:
                last_approval = None

        write_document(self.username, pr_reports)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog="ProjectStatus",
        description="Generates project status reports",
    )
    parser.add_argument("username")
    parser.add_argument("name")
    args = parser.parse_args()
    print(args)
    try:
        EhrProjectStatus(
            "biostat821-2026", args.username.strip(), args.name.strip()
        ).generate_project_report()
    except Exception as e:
        print(f"Failed to generate PR summaries. {e}")
