"""Client for interacting with the GitHub API."""

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Self
from zoneinfo import ZoneInfo

import httpx


def et_datetime(iso: str) -> datetime:
    """Parse ISO format as datetime in Eastern time."""
    return datetime.fromisoformat(iso).astimezone(ZoneInfo("America/New_York"))


@dataclass
class Event:
    created_at: datetime
    type: str
    reviewer: str | None = None

    def get_summary(self: Self, verbose: bool = False) -> str:
        return f"{self.creation_time} & {self.type}{f' from {self.reviewer}' if self.reviewer else ''}"

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

    @staticmethod
    def from_dict(pr, username, main_id):
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


def get_event(timeline_item) -> Event:
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


def parse_events(pr) -> list[Event]:
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
        in ("patrickkwang", "Surgulaze99", "skylershapiro")
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

    return sorted(
        reviews_requested
        + reviews
        + review_requests_removed
        + reviews_dismissed
        + merges
        + closes,
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

    def get_repo_name(self: Self, username: str) -> str:
        return f"ehr-utils-{username}"

    def list_prs(self: Self, usernames: list[str]) -> dict[str, list[PullRequest]]:
        """Get data for PRs."""
        results = dict()
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
                                        }}
                                    }}
                                }}
                            }}
                        }}
                    }}
                }}
            """
        response = httpx.post(
            "https://api.github.com/graphql",
            headers=self.headers,
            json={
                "query": f"""
                {{{repo_pieces}}}
                """
            },
        )
        for username, repo_name in repos_by_username.items():
            repo_data = response.json()["data"][repo_name]
            if repo_data is None:
                return {}
            main_id = repo_data["defaultBranchRef"]["target"]["id"]
            results[username] = sorted(
                [
                    PullRequest.from_dict(edge["node"], username, main_id)
                    for edge in repo_data["pullRequests"]["edges"]
                ],
                key=lambda pr: pr.created_at,
            )

        return results
