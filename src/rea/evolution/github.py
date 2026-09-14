from __future__ import annotations

from typing import Any

import httpx

from ..github import GitHubClient
from .contracts import EvolutionHypothesis, Lesson


class GitHubEvolutionClient(GitHubClient):
    def create_hypothesis_issue(
        self,
        hypothesis: EvolutionHypothesis,
        lessons: list[Lesson],
    ) -> int:
        lesson_text = "\n".join(f"- {item.statement}" for item in lessons) or "- none"
        evidence = "\n".join(f"- {item}" for item in hypothesis.evidence)
        issue = self.create_issue(
            hypothesis.repository,
            title=f"REA evolution: {hypothesis.problem[:100]}",
            body=(
                "## Autonomous evolution hypothesis\n\n"
                f"**ID:** `{hypothesis.id}`\n"
                f"**Problem:** {hypothesis.problem}\n"
                f"**Hypothesis:** {hypothesis.hypothesis}\n"
                f"**Target:** {hypothesis.baseline:.4f} → {hypothesis.target:.4f}\n"
                f"**Risk:** {hypothesis.risk}\n\n"
                f"### Evidence\n{evidence}\n\n"
                f"### Relevant lessons\n{lesson_text}\n"
            ),
        )
        return int(issue["number"])

    def changed_paths(self, repository: str, base: str, head: str) -> list[str]:
        response = httpx.get(
            f"{self.base_url}/repos/{repository}/compare/{base}...{head}",
            headers=self._headers(),
            timeout=30,
        )
        response.raise_for_status()
        return [str(item["filename"]) for item in response.json().get("files", [])]

    def pull_request_state(self, repository: str, number: int) -> dict[str, Any]:
        response = httpx.get(
            f"{self.base_url}/repos/{repository}/pulls/{number}",
            headers=self._headers(),
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        return {
            "state": data["state"],
            "merged": bool(data.get("merged")),
            "mergeable": data.get("mergeable"),
            "draft": bool(data.get("draft")),
            "sha": data["head"]["sha"],
            "node_id": data["node_id"],
        }

    def branch_protected(self, repository: str, branch: str = "main") -> bool:
        response = httpx.get(
            f"{self.base_url}/repos/{repository}/branches/{branch}/protection",
            headers=self._headers(),
            timeout=30,
        )
        if response.status_code != 200:
            return False
        data = response.json()
        checks = data.get("required_status_checks") or {}
        contexts = checks.get("contexts") or []
        enforce_admins = (data.get("enforce_admins") or {}).get("enabled", False)
        return bool(contexts) and bool(enforce_admins)

    def mark_ready(self, pull_request_node_id: str) -> None:
        response = httpx.post(
            "https://api.github.com/graphql",
            headers=self._headers(),
            json={
                "query": (
                    "mutation($id:ID!){markPullRequestReadyForReview("
                    "input:{pullRequestId:$id}){pullRequest{isDraft}}}"
                ),
                "variables": {"id": pull_request_node_id},
            },
            timeout=30,
        )
        response.raise_for_status()
        if response.json().get("errors"):
            raise RuntimeError("GitHub refused to mark the pull request ready")

    def checks_green(
        self,
        repository: str,
        sha: str,
        required_checks: tuple[str, ...] = ("validate",),
    ) -> bool:
        response = httpx.get(
            f"{self.base_url}/repos/{repository}/commits/{sha}/check-runs",
            headers={**self._headers(), "Accept": "application/vnd.github+json"},
            timeout=30,
        )
        response.raise_for_status()
        runs = response.json().get("check_runs", [])
        by_name = {str(item.get("name")): item for item in runs}
        if any(name not in by_name for name in required_checks):
            return False
        return all(
            by_name[name].get("status") == "completed"
            and by_name[name].get("conclusion") == "success"
            for name in required_checks
        )

    def evolution_records(self, repository: str) -> list[dict]:
        runs = httpx.get(
            f"{self.base_url}/repos/{repository}/actions/runs",
            headers=self._headers(),
            params={"per_page": 50},
            timeout=30,
        )
        runs.raise_for_status()
        records = []
        for run in runs.json().get("workflow_runs", []):
            if run.get("conclusion") != "failure":
                continue
            records.append(
                {
                    "id": f"github-run-{run['id']}",
                    "event": "ci.failed",
                    "timestamp": run.get("updated_at"),
                    "data": {
                        "summary": f"{run.get('name', 'CI')} failed",
                        "fingerprint": f"ci:{run.get('workflow_id')}:{run.get('name')}",
                        "evidence": [run.get("html_url", "")],
                    },
                }
            )
        return records

    def merge(self, repository: str, number: int, expected_sha: str) -> bool:
        response = httpx.put(
            f"{self.base_url}/repos/{repository}/pulls/{number}/merge",
            headers=self._headers(),
            json={"sha": expected_sha, "merge_method": "squash"},
            timeout=30,
        )
        response.raise_for_status()
        return bool(response.json().get("merged"))
