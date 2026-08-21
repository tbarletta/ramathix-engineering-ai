from __future__ import annotations

from pathlib import Path

from ..execution import GovernedLocalRunner
from .models import GitSummary


class GitHistoryAnalyzer:
    def __init__(self, runner: GovernedLocalRunner) -> None:
        self.runner = runner

    def summarize(self, root: Path, *, limit: int = 20) -> GitSummary:
        if not (root / ".git").exists():
            return GitSummary()

        branch = self.runner.run(
            ["git", "branch", "--show-current"], cwd=root
        )
        log = self.runner.run(
            [
                "git",
                "log",
                f"-{limit}",
                "--date=iso-strict",
                "--pretty=format:%H%x1f%ad%x1f%an%x1f%s",
            ],
            cwd=root,
        )
        commits: list[dict[str, str]] = []
        if log.returncode == 0:
            for line in log.stdout.splitlines():
                parts = line.split("\x1f", maxsplit=3)
                if len(parts) == 4:
                    commits.append(
                        {"sha": parts[0], "date": parts[1], "author": parts[2], "subject": parts[3]}
                    )
        return GitSummary(
            branch=branch.stdout.strip() or None if branch.returncode == 0 else None,
            commits_sampled=len(commits),
            recent_commits=commits,
        )
