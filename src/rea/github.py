from __future__ import annotations

import os
import re
from typing import Any

import httpx

from .domain import IssueWorkUnit

_GITHUB_TOKEN_PATTERN = re.compile(r"[^\s()<>\[\]{}]*github\.com[^\s()<>\[\]{}]*", re.IGNORECASE)


def parse_github_slug(url: str) -> str | None:
    """Extract an ``owner/repo`` slug from a GitHub URL or SSH remote."""
    value = url.strip().rstrip("/")
    if "github.com:" in value:
        value = value.split("github.com:", maxsplit=1)[1]
    elif "github.com/" in value:
        value = value.split("github.com/", maxsplit=1)[1]
    else:
        return None
    slug = value.removesuffix(".git")
    parts = slug.split("/")
    if len(parts) != 2 or not all(parts):
        return None
    return "/".join(parts)


def extract_github_reference(message: str) -> str | None:
    """Find the first ``owner/repo`` slug mentioned as a github.com link in free text."""
    for token in _GITHUB_TOKEN_PATTERN.findall(message):
        slug = parse_github_slug(token.strip(".,;:'\""))
        if slug:
            return slug
    return None


class GitHubClient:
    def __init__(self, token: str | None = None, base_url: str = "https://api.github.com") -> None:
        self.token = token or os.getenv("GITHUB_TOKEN")
        self.base_url = base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def get_issue(self, repository: str, number: int) -> IssueWorkUnit:
        response = httpx.get(
            f"{self.base_url}/repos/{repository}/issues/{number}",
            headers=self._headers(),
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()
        if "pull_request" in data:
            raise ValueError(f"#{number} is a pull request, not an issue")
        return IssueWorkUnit(
            repository=repository,
            number=number,
            title=data["title"],
            body=data.get("body") or "",
            url=data["html_url"],
            labels=tuple(label["name"] for label in data.get("labels", [])),
        )

    def create_issue(
        self,
        repository: str,
        *,
        title: str,
        body: str,
    ) -> dict[str, Any]:
        response = httpx.post(
            f"{self.base_url}/repos/{repository}/issues",
            headers=self._headers(),
            json={"title": title, "body": body},
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json()

    def create_pull_request(
        self,
        repository: str,
        *,
        title: str,
        body: str,
        head: str,
        base: str,
        draft: bool = True,
    ) -> dict[str, Any]:
        response = httpx.post(
            f"{self.base_url}/repos/{repository}/pulls",
            headers=self._headers(),
            json={
                "title": title,
                "body": body,
                "head": head,
                "base": base,
                "draft": draft,
            },
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json()

    def ensure_pull_request(
        self,
        repository: str,
        *,
        title: str,
        body: str,
        head: str,
        base: str,
        draft: bool = True,
    ) -> dict[str, Any]:
        owner = repository.split("/", 1)[0]
        response = httpx.get(
            f"{self.base_url}/repos/{repository}/pulls",
            headers=self._headers(),
            params={
                "state": "open",
                "head": f"{owner}:{head}",
                "base": base,
                "per_page": 10,
            },
            timeout=30.0,
        )
        response.raise_for_status()
        matches = response.json()
        if matches:
            return matches[0]
        return self.create_pull_request(
            repository,
            title=title,
            body=body,
            head=head,
            base=base,
            draft=draft,
        )
