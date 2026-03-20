#!/usr/bin/env python
"""Utilities for analyzing and reporting EHR project status."""

from __future__ import annotations

import argparse
import base64
import csv
import json
import math
import re
import traceback
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Self
from zoneinfo import ZoneInfo

from github_client import (
    Event,
    GithubClient,
    PullRequest,
)

# from latex_rendering import write_document
from typst_rendering import write_document
from pr_state_machine import PrStateMachine
from project_util import NUM_PHASES, PHASES, DocumentSpec, PrState, now, td_to_str


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


def et_datetime(iso: str) -> datetime:
    """Parse ISO format as datetime in Eastern time."""
    return datetime.fromisoformat(iso).astimezone(ZoneInfo("America/New_York"))


@dataclass
class Extension:
    name: str
    username: str
    phase: int
    due_date: datetime


def get_extensions(filename: str) -> list[Extension]:
    """Read extensions from file."""
    file_path = Path(filename)
    if not file_path.is_file():
        return []

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
    file_path = Path(filename)
    if not file_path.is_file():
        return {}

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


@dataclass
class DueDate:
    due_date: datetime
    original_due_date: datetime
    extension: Extension | None


class EhrProjectStatus:
    """Multitool for managing EHR projects on GitHub."""

    def __init__(
        self: Self,
        username: str,
        name: str,
        prs: dict[str, list[PullRequest]],
        outputs_path: Path,
        cache_path: Path = Path("pr_cache"),
    ):
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
        self.prs = prs
        self.outputs_path = outputs_path
        self.cache_path = cache_path
        self.cache_path.mkdir(parents=True, exist_ok=True)

    start_time = datetime(2026, 2, 13, 23, 59, 59, tzinfo=ZoneInfo("America/New_York"))
    phase_time_budget = timedelta(days=7)

    def _generate_pr_report(
        self,
        pr: PullRequest,
        phase: int | None = None,
        last_approval: datetime | None = None,
    ) -> tuple[DocumentSpec, datetime | None, dict[str, Any]]:
        """Generate PR summary."""
        phase_start_time = last_approval if last_approval else self.start_time
        all_events = sorted(
            [Event(pr.created_at, type="CREATED")]
            + pr.timeline_events
            + (
                [Event(last_approval, type="PREVIOUS_PHASE_APPROVED")]
                if last_approval
                else []
            ),
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

        doc_spec = DocumentSpec(
            entries,
            pr_state_machine.total_under_development_duration,
            pr_state_machine.total_under_review_duration,
            late_by,
            points_deducted,
        )
        waiting_for = timedelta(0)
        if (
            pr_state_machine.last_review_requested
            and pr_state_machine.state == PrState.UNDER_REVIEW
        ):
            waiting_for = now() - pr_state_machine.last_review_requested
        summary = {
            "name": self.name,
            "username": self.username,
            "phase": phase,
            "pr": pr.permalink,
            "state": pr_state_machine.state.value,
            "late_by": late_by,
            "waiting_for": waiting_for,
            "lead_reviewer": "",
        }
        return doc_spec, approval, summary

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
        prs = [
            pr
            for pr in self.prs[self.username]
            if pr.based_on_main and not pr.just_workflows
        ]
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

    def generate_project_report(self: Self) -> list[dict[str, Any]]:
        """Generate PR summaries."""
        phase_prs = self._get_phase_prs()
        pr_reports = []
        summaries = []

        last_approval = None
        for phase, prs in sorted(phase_prs.items()):
            for pr in prs:
                doc_spec, approval, summary = self._generate_pr_report(
                    pr, phase, last_approval
                )
                pr_reports.append((phase, pr, doc_spec))
                summaries.append(summary)
                if doc_spec.points_deducted is not None:
                    with open(self.outputs_path / "_summary.csv", "a") as f:
                        f.write(
                            f'"{self.name}",{self.username},{phase},{pr.permalink},{100 - doc_spec.points_deducted}\n'
                        )
            if approval and phase < NUM_PHASES:
                last_approval = approval
            else:
                last_approval = None

        write_document(self.username, pr_reports, self.outputs_path)

        return summaries


def get_data(
    organization: str, students: list[dict[str, Any]], use_cache: bool = False
) -> tuple[dict[str, Any], Path | None]:
    cache_path = Path("pr_cache")
    if use_cache:
        # get latest file
        latest_file = max(
            cache_path.glob("*.json"), key=lambda file: file.name
        ).resolve()
        with open(latest_file) as f:
            return json.load(f), latest_file

    github_client = GithubClient(organization)
    prs = github_client.list_prs([student["username"] for student in students])
    pr_dicts = {username: [pr.to_dict() for pr in prs] for username, prs in prs.items()}
    pr_filename = None
    pr_filename = (
        cache_path
        / f"pull_requests_{datetime.strftime(datetime.now(), '%Y%m%d%H%M%S')}.json"
    )
    with open(pr_filename, "w") as f:
        json.dump(
            pr_dicts,
            f,
            indent=4,
        )
    return pr_dicts, pr_filename


def push_summaries(all_summaries: list[dict[str, Any]]) -> None:
    # sort status_summary rows by:
    # - state: under review, under development, merged, then closed
    # - waiting_for (decreasing)
    # - username
    # - pr (decreasing)
    all_summaries = sorted(
        all_summaries,
        key=lambda row: (
            row["state"],
            row["waiting_for"],
            row["username"],
            row["pr"],
        ),
        reverse=True,
    )
    for row in all_summaries:
        row["waiting_for"] = td_to_str(row["waiting_for"])
        row["late_by"] = td_to_str(row["late_by"])

    github_client = GithubClient(organization)
    response = github_client.read_file("ehr-project-status", "status_summary.csv")
    sha = response["sha"]

    # get lead reviewers
    latest_status_summary = base64.b64decode(response["content"]).decode()
    reader = csv.DictReader(latest_status_summary.split("\n"))
    latest_rows = list(reader)
    lead_reviewer_by_pr = {row["pr"]: row["lead_reviewer"] for row in latest_rows}
    # update summaries with lead reviewers
    for row in all_summaries:
        row["lead_reviewer"] = lead_reviewer_by_pr.get(row["pr"]) or ""

    # write local status_summary.csv
    with open("outputs/status_summary.csv", "w") as f:
        writer = csv.DictWriter(f, list(all_summaries[0].keys()))
        writer.writeheader()
        writer.writerows(all_summaries)
    print("outputs/status_summary.csv")

    # write status_summary.csv to GitHub
    with open("outputs/status_summary.csv", "rb") as f:
        content = f.read()
    github_client.upload_file("ehr-project-status", "status_summary.csv", sha, content)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog="ProjectStatus",
        description="Generates project status reports",
    )
    parser.add_argument("--filename")
    parser.add_argument("--username")
    parser.add_argument("--use_cache", action="store_true")
    parser.add_argument("--push_summaries", action="store_true")
    args = parser.parse_args()
    if args.filename:
        with open(args.filename) as f:
            csvreader = csv.DictReader(f)
            students = list(csvreader)
    elif args.username:
        students = [{"email": "", "name": "", "username": args.username}]

    organization = "biostat821-2026"
    pr_dicts, _ = get_data(organization, students, args.use_cache)

    prs = {
        username: [PullRequest.from_dict(pr) for pr in prs]
        for username, prs in pr_dicts.items()
    }

    outputs_path = Path("outputs")
    outputs_path.mkdir(parents=True, exist_ok=True)
    all_summaries = []
    try:
        for student in students:
            summaries = EhrProjectStatus(
                student["username"], student["name"], prs, outputs_path=outputs_path
            ).generate_project_report()
            all_summaries.extend(summaries)
    except Exception:
        print("Failed to generate PR reports:")
        traceback.print_exc()

    if args.push_summaries:
        push_summaries(all_summaries)
