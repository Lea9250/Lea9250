#!/usr/bin/env python3
"""Refresh the public repositories contributed to during the last 12 months."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path


API_URL = "https://api.github.com/graphql"
START_MARKER = "<!-- recent-repositories:start -->"
END_MARKER = "<!-- recent-repositories:end -->"
README_PATH = Path(__file__).resolve().parents[1] / "README.md"
DISPLAY_LIMIT = 15

# These labels describe the part of each repository that is relevant to Léa's
# work. They are intentionally curated instead of inferred from GitHub Linguist.
REPOSITORY_STACKS = {
    "OCSInventory-NG/OCSInventory-Server-Backend-Rework": "Python/Django",
    "OCSInventory-NG/OCSInventory-Server-Frontend-Rework": "Vue/JavaScript",
    "OCSInventory-NG/OCSInventory-Agent-Rework": "Dart/Flutter",
    "OCSInventory-NG/OCSInventory-Server-Packages": "Deb/RPM/Docker/Shell",
    "OCSInventory-NG/OCSInventory-SNMP-Scanner": "Python",
    "OCSInventory-NG/Macosx-Packager": "Objective-C",
    "OCSInventory-NG/UnixAgent": "Perl",
    "OCSInventory-NG/OCSInventory-ocsreports": "PHP/JavaScript",
    "OCSInventory-NG/Wiki": "Markdown",
    "PluginsOCSInventory-NG/firewallrules": "Perl",
    "OCSInventory-NG/OCSInventory-Loadtest-Rework": "Python",
    "PluginsOCSInventory-NG/officepack": "VBScript",
}

QUERY = """
query RecentRepositories($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      commits: commitContributionsByRepository(maxRepositories: 25) {
        repository { nameWithOwner url isPrivate }
        contributions(first: 1) { totalCount }
      }
      issues: issueContributionsByRepository(maxRepositories: 25) {
        repository { nameWithOwner url isPrivate }
        contributions(first: 1) { totalCount }
      }
      pullRequests: pullRequestContributionsByRepository(maxRepositories: 25) {
        repository { nameWithOwner url isPrivate }
        contributions(first: 1) { totalCount }
      }
      reviews: pullRequestReviewContributionsByRepository(maxRepositories: 25) {
        repository { nameWithOwner url isPrivate }
        contributions(first: 1) { totalCount }
      }
    }
  }
}
"""


def iso_timestamp(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def fetch_contributions(token: str, login: str, start: datetime, end: datetime) -> dict:
    payload = json.dumps(
        {
            "query": QUERY,
            "variables": {
                "login": login,
                "from": iso_timestamp(start),
                "to": iso_timestamp(end),
            },
        }
    ).encode()
    request = urllib.request.Request(
        API_URL,
        data=payload,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "Lea9250-profile-readme",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub GraphQL request failed: {error.code} {details}") from error

    if result.get("errors"):
        messages = "; ".join(error["message"] for error in result["errors"])
        raise RuntimeError(f"GitHub GraphQL returned errors: {messages}")

    user = result.get("data", {}).get("user")
    if user is None:
        raise RuntimeError(f"GitHub user {login!r} was not found")
    return user["contributionsCollection"]


def aggregate_repositories(collection: dict) -> list[dict]:
    repositories: dict[str, dict] = {}
    categories = {
        "commits": "commits",
        "issues": "issues",
        "pullRequests": "pull_requests",
        "reviews": "reviews",
    }

    for source, destination in categories.items():
        for contribution in collection[source]:
            repository = contribution["repository"]
            # A token with broader scopes may see private repositories. Never put
            # their names or activity into a README that will eventually be public.
            if repository["isPrivate"]:
                continue

            name = repository["nameWithOwner"]
            item = repositories.setdefault(
                name,
                {
                    "name": name,
                    "url": repository["url"],
                    "stack": REPOSITORY_STACKS.get(name),
                    "commits": 0,
                    "issues": 0,
                    "pull_requests": 0,
                    "reviews": 0,
                },
            )
            item[destination] += contribution["contributions"]["totalCount"]

    return sorted(
        repositories.values(),
        key=lambda item: (-sum(item[key] for key in categories.values()), item["name"].lower()),
    )


def count_label(count: int, singular: str, plural: str | None = None) -> str:
    return f"{count} {singular if count == 1 else plural or singular + 's'}"


def render_list(repositories: list[dict], start: datetime, end: datetime) -> str:
    lines = []
    for repository in repositories[:DISPLAY_LIMIT]:
        activity = []
        if repository["stack"]:
            activity.append(f'`{repository["stack"]}`')
        if repository["commits"]:
            activity.append(count_label(repository["commits"], "commit"))
        if repository["pull_requests"]:
            activity.append(count_label(repository["pull_requests"], "PR"))
        if repository["reviews"]:
            activity.append(count_label(repository["reviews"], "review"))
        if repository["issues"]:
            activity.append(count_label(repository["issues"], "issue"))
        details = " · ".join(activity)
        lines.append(f'- [{repository["name"]}]({repository["url"]}) · {details}')

    if not lines:
        lines.append("_No public repository contributions found in this period._")

    visible_count = min(len(repositories), DISPLAY_LIMIT)
    if len(repositories) > DISPLAY_LIMIT:
        summary = f"Showing the {visible_count} most active public repositories"
    else:
        summary = f"Showing all {visible_count} public repositories"

    lines.extend(
        [
            "",
            (
                f"<sub>{summary} from {start:%-d %B %Y} through "
                f"{end:%-d %B %Y}; refreshed weekly.</sub>"
            ),
        ]
    )
    return "\n".join(lines)


def update_readme(rendered: str) -> None:
    readme = README_PATH.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"{re.escape(START_MARKER)}.*?{re.escape(END_MARKER)}",
        flags=re.DOTALL,
    )
    replacement = f"{START_MARKER}\n{rendered}\n{END_MARKER}"
    updated, replacements = pattern.subn(replacement, readme, count=1)
    if replacements != 1:
        raise RuntimeError("Recent repository markers are missing or duplicated in README.md")
    README_PATH.write_text(updated, encoding="utf-8")


def main() -> None:
    token = os.environ.get("GH_TOKEN")
    if not token:
        raise RuntimeError("GH_TOKEN is required")

    login = os.environ.get("GITHUB_USER", "Lea9250")
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=365)
    collection = fetch_contributions(token, login, start, end)
    repositories = aggregate_repositories(collection)
    update_readme(render_list(repositories, start, end))
    print(f"Updated README with {min(len(repositories), DISPLAY_LIMIT)} public repositories")


if __name__ == "__main__":
    main()
