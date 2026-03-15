"""Client for interacting with the GitHub API."""

from __future__ import annotations

import base64
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Self
from zoneinfo import ZoneInfo

import httpx
from tqdm import tqdm


def et_datetime(iso: str) -> datetime:
    """Parse ISO format as datetime in Eastern time."""
    return datetime.fromisoformat(iso).astimezone(ZoneInfo("America/New_York"))


@dataclass
class Event:
    created_at: datetime
    type: str
    reviewer: str | None = None

    def get_summary(self: Self, verbose: bool = False) -> str:
        return f"{self.type}{f' from {self.reviewer}' if self.reviewer else ''}"

    @property
    def creation_time(self: Self) -> str:
        return self.created_at.strftime("%Y-%m-%d %H:%M:%S")

    def to_dict(self) -> dict[str, str | None]:
        return {
            "created_at": self.created_at.isoformat(),
            "type": self.type,
            "reviewer": self.reviewer,
        }

    @staticmethod
    def from_dict(serialized: dict[str, str]) -> Event:
        return Event(
            created_at=datetime.fromisoformat(serialized["created_at"]),
            type=serialized["type"],
            reviewer=serialized["reviewer"],
        )


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

    @staticmethod
    def from_github_dict(
        pr: dict[str, Any], username: str, main_id: str
    ) -> PullRequest:
        return PullRequest(
            username,
            branch=pr["headRefName"],
            created_at=et_datetime(pr["createdAt"]),
            title=pr["title"],
            permalink=pr["permalink"],
            number=pr["number"],
            state=pr["state"],
            # baseRef can be None if the base branch has been deleted
            based_on_main=(
                base_id := pr["baseRef"]["target"]["id"] if pr["baseRef"] else None
            )
            == main_id,
            behind_base=base_id
            not in (
                [
                    node["id"]
                    for node in pr["commits"]["nodes"][0]["commit"]["history"]["nodes"]
                ]
                if pr["commits"]["nodes"]  # there may be no commits
                else []
            ),
            timeline_events=parse_events(pr),
        )

    def to_dict(self) -> dict[str, str | int | list[dict[str, str | None]]]:
        return {
            "owner": self.owner,
            "branch": self.branch,
            "created_at": self.created_at.isoformat(),
            "title": self.title,
            "permalink": self.permalink,
            "number": self.number,
            "state": self.state,
            "based_on_main": self.based_on_main,
            "behind_base": self.behind_base,
            "timeline_events": [event.to_dict() for event in self.timeline_events],
        }

    @staticmethod
    def from_dict(serialized: dict[str, Any]) -> PullRequest:
        return PullRequest(
            owner=serialized["owner"],
            branch=serialized["branch"],
            created_at=datetime.fromisoformat(serialized["created_at"]),
            title=serialized["title"],
            permalink=serialized["permalink"],
            number=serialized["number"],
            state=serialized["state"],
            based_on_main=serialized["based_on_main"],
            behind_base=serialized["behind_base"],
            timeline_events=[
                Event.from_dict(event_dict)
                for event_dict in serialized["timeline_events"]
            ],
        )


def get_event(timeline_item: dict[str, Any]) -> Event:
    # DISMISSED is also considered approval in case a review was APPROVED and subsequently DISMISSED.
    if timeline_item["state"] in ("APPROVED", "DISMISSED"):
        return Event(
            created_at=et_datetime(timeline_item["createdAt"]),
            reviewer=timeline_item["author"]["login"],
            type="APPROVED",
        )
    if timeline_item["state"] == ("CHANGES_REQUESTED"):
        return Event(
            created_at=et_datetime(timeline_item["createdAt"]),
            reviewer=timeline_item["author"]["login"],
            type="CHANGES_REQUESTED",
        )
    if timeline_item["state"] == ("COMMENTED"):
        return Event(
            created_at=et_datetime(timeline_item["createdAt"]),
            reviewer=timeline_item["author"]["login"],
            type="COMMENTED",
        )
    raise ValueError(f"Unrecognized review type {timeline_item}")


def parse_events(pr: dict[str, Any]) -> list[Event]:
    timeline_items = [edge["node"] for edge in pr["timelineItems"]["edges"]]
    reviews_requested = [
        Event(
            created_at=et_datetime(timeline_item["createdAt"]),
            reviewer=timeline_item["requestedReviewer"]["login"],
            type="REVIEW_REQUESTED",
        )
        for timeline_item in timeline_items
        if timeline_item["__typename"] == "ReviewRequestedEvent"
        and "login"
        in timeline_item[
            "requestedReviewer"
        ]  # there is no "login" if the reviewer is Copilot
    ]
    reviews_dismissed = [
        Event(
            created_at=et_datetime(timeline_item["createdAt"]),
            reviewer=timeline_item["review"]["author"]["login"],
            type="REVIEW_DISMISSED",
        )
        for timeline_item in timeline_items
        if timeline_item["__typename"] == "ReviewDismissedEvent"
    ]
    review_requests_removed = [
        Event(
            created_at=et_datetime(timeline_item["createdAt"]),
            reviewer=timeline_item["requestedReviewer"]["login"],
            type="REVIEW_REQUEST_REMOVED",
        )
        for timeline_item in timeline_items
        if timeline_item["__typename"] == "ReviewRequestRemovedEvent"
        and "login"
        in timeline_item[
            "requestedReviewer"
        ]  # there is no "login" if the reviewer is Copilot
    ]
    reviews = [
        get_event(timeline_item)
        for timeline_item in timeline_items
        if timeline_item["__typename"] == "PullRequestReview"
        and timeline_item["author"]["login"]
        in ("patrickkwang", "Surguladze99", "skylershapiro")
    ]
    merges = [
        Event(created_at=et_datetime(timeline_item["createdAt"]), type="MERGED")
        for timeline_item in timeline_items
        if timeline_item["__typename"] == "MergedEvent"
    ]
    closes = [
        Event(created_at=et_datetime(timeline_item["createdAt"]), type="CLOSED")
        for timeline_item in timeline_items
        if timeline_item["__typename"] == "ClosedEvent"
    ]

    reopens = [
        Event(created_at=et_datetime(timeline_item["createdAt"]), type="REOPENED")
        for timeline_item in timeline_items
        if timeline_item["__typename"] == "ReopenedEvent"
    ]
    other = [
        timeline_item
        for timeline_item in timeline_items
        if timeline_item["__typename"]
        not in (
            "ClosedEvent",
            "ReopenedEvent",
            "MergedEvent",
            "IssueComment",  # ignore these
            "PullRequestCommit",  # ignore these
            "PullRequestRevisionMarker",  # ignore these
            "AssignedEvent",  # ignore these
            "UnassignedEvent",  # ignore these
            "MentionedEvent",  # ignore these
            "SubscribedEvent",  # ignore these
            "ConvertToDraftEvent",  # ignore these
            "ReadyForReviewEvent",  # ignore these
            "HeadRefDeletedEvent",  # ???
            "HeadRefForcePushedEvent",  # ???
            "HeadRefRestoredEvent",  # ???
            "CrossReferencedEvent",  # ???
            "CommentDeletedEvent",  # ignore these
            "RenamedTitleEvent",  # ignore these
            "PullRequestReview",
            "ReviewRequestedEvent",
            "ReviewDismissedEvent",
            "ReviewRequestRemovedEvent",
        )
    ]
    if other:
        raise ValueError(other)

    return sorted(
        reviews_requested
        + reviews
        + review_requests_removed
        + reviews_dismissed
        + merges
        + closes
        + reopens,
        key=lambda event: event.created_at,
    )


class GithubClient:
    """Client for interacting with the GitHub API."""

    def __init__(self: Self, organization: str):
        """Initialize."""
        self.organization = organization
        self.auth_token = os.getenv("GITHUB_TOKEN")
        self.headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.auth_token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def read_file(
        self,
        repo: str,
        filepath: str,
    ) -> Any:
        endpoint = f"https://api.github.com/repos/{self.organization}/{repo}/contents/{filepath}"
        response = httpx.get(
            endpoint,
            headers=self.headers,
            timeout=20.0,
        )
        return response.json()

    def upload_file(
        self,
        repo: str,
        filepath: str,
        sha: str,
        content: bytes,
        commit_message: str | None = None,
    ) -> None:
        if commit_message is None:
            commit_message = f"Update {filepath}"
        base64_content = base64.b64encode(content)
        endpoint = f"https://api.github.com/repos/{self.organization}/{repo}/contents/{filepath}"
        httpx.put(
            endpoint,
            headers=self.headers,
            json={
                "message": commit_message,
                "committer": {
                    "name": "Patrick Wang",
                    "email": "patrickkwang@users.noreply.github.com",
                },
                "sha": sha,
                "content": base64_content.decode("ascii"),
            },
            timeout=20.0,
        )

    def get_repo_name(self: Self, username: str) -> str:
        return f"ehr-utils-{username}"

    def generate_query(self, usernames: list[str]) -> tuple[str, dict[str, str]]:
        repo_pieces = ""
        repos_by_username = dict()
        for idx, username in enumerate(usernames):
            repo_name = f"repo{idx:03d}"
            repos_by_username[username] = repo_name
            repo_pieces += f"""
                {repo_name}: repository(owner: "{self.organization}", name: "{self.get_repo_name(username)}") {{
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
                                            ... on ReopenedEvent {{
                                                createdAt
                                            }}
                                        }}
                                    }}
                                }}
                            }}
                        }}
                    }}
                }}
            """
        return (
            f"""
            {{{repo_pieces}}}
            """,
            repos_by_username,
        )

    def list_prs(self: Self, usernames: list[str]) -> dict[str, list[PullRequest]]:
        """Get data for PRs."""
        results: dict[str, list[PullRequest]] = dict()
        start_idx = 0
        batch_size = 10

        with tqdm(total=len(usernames)) as pbar:
            while start_idx < len(usernames):
                username_batch = usernames[start_idx : start_idx + batch_size]
                query, repos_by_username = self.generate_query(username_batch)
                for timeout_seconds in (1, 2, 4, 8, 16):  # exponential backoff
                    response = httpx.post(
                        "https://api.github.com/graphql",
                        headers=self.headers,
                        json={"query": query},
                        timeout=20.0,
                    )
                    if response.status_code == 200:
                        break
                    print(f"Trying again in {timeout_seconds} seconds...")
                    time.sleep(timeout_seconds)
                if timeout_seconds == 16:
                    raise RuntimeError(
                        f"Failed to use API.\n json: {response.json()}\n header: {response.headers}"
                    )
                for username, repo_name in repos_by_username.items():
                    repo_data = response.json()["data"][repo_name]
                    if repo_data is None:
                        return {}
                    main_id = repo_data["defaultBranchRef"]["target"]["id"]
                    results[username] = sorted(
                        [
                            PullRequest.from_github_dict(
                                edge["node"], username, main_id
                            )
                            for edge in repo_data["pullRequests"]["edges"]
                        ],
                        key=lambda pr: pr.created_at,
                    )
                start_idx += batch_size
                pbar.update(len(username_batch))

        return results
