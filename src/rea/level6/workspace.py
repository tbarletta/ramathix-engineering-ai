from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..audit import AuditLog
from ..execution import GovernedLocalRunner


class WorkspaceBoundaryError(RuntimeError):
    pass


@dataclass(frozen=True)
class Worktree:
    path: Path
    branch: str
    base_branch: str


class WorkspaceManager:
    def __init__(
        self,
        *,
        source_root: Path,
        worktree_root: Path,
        runner: GovernedLocalRunner,
        audit: AuditLog,
    ) -> None:
        self.source_root = source_root.resolve()
        self.worktree_root = worktree_root.resolve()
        self.runner = runner
        self.audit = audit

    def prepare(
        self,
        *,
        issue_number: int,
        issue_title: str,
        base_branch: str,
    ) -> Worktree:
        branch = f"rea/issue-{issue_number}-{_slug(issue_title)}"[:120].rstrip("-")
        path = (self.worktree_root / f"issue-{issue_number}").resolve()
        self._require_under_root(path, self.worktree_root)

        if path.exists():
            result = self.runner.run(
                ["git", "branch", "--show-current"],
                cwd=path,
                actor="workspace_manager",
            )
            current = result.stdout.strip()
            if current != branch:
                raise RuntimeError(
                    f"existing worktree uses branch {current!r}, expected {branch!r}"
                )
            return Worktree(path=path, branch=branch, base_branch=base_branch)

        self.worktree_root.mkdir(parents=True, exist_ok=True)
        branch_lookup = self.runner.run(
            ["git", "branch", "--list", branch],
            cwd=self.source_root,
            actor="workspace_manager",
        )
        branch_exists = bool(branch_lookup.stdout.strip())
        argv = ["git", "worktree", "add"]
        if not branch_exists:
            argv.extend(["-b", branch, str(path), base_branch])
        else:
            argv.extend([str(path), branch])
        result = self.runner.run(
            argv,
            cwd=self.source_root,
            actor="workspace_manager",
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "git worktree add failed")
        self.audit.write(
            "level6.worktree.created",
            actor="workspace_manager",
            data={"path": str(path), "branch": branch, "base": base_branch},
        )
        return Worktree(path=path, branch=branch, base_branch=base_branch)

    def changed_paths(self, worktree: Worktree) -> list[str]:
        result = self.runner.run(
            ["git", "status", "--porcelain=v1", "-z"],
            cwd=worktree.path,
            actor="workspace_manager",
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "git status failed")
        parts = result.stdout.split("\0")
        paths: list[str] = []
        index = 0
        while index < len(parts):
            record = parts[index]
            index += 1
            if not record:
                continue
            if len(record) < 4:
                raise RuntimeError(f"unexpected git status record: {record!r}")
            status = record[:2]
            path = record[3:]
            paths.append(self._safe_relative(path))
            if ("R" in status or "C" in status) and index < len(parts):
                renamed = parts[index]
                index += 1
                if renamed:
                    paths.append(self._safe_relative(renamed))
        return list(dict.fromkeys(paths))


    def assert_only_allowed_changes(
        self,
        worktree: Worktree,
        allowed_paths: set[str],
    ) -> None:
        allowed = {self._safe_relative(path) for path in allowed_paths}
        changed = set(self.changed_paths(worktree))
        unexpected = sorted(changed.difference(allowed))
        if unexpected:
            self.audit.write(
                "level6.boundary_violation",
                actor="workspace_manager",
                data={"paths": unexpected},
            )
            raise WorkspaceBoundaryError(
                "validation changed files outside the approved plan: "
                + ", ".join(unexpected)
            )

    def stage(self, worktree: Worktree) -> list[str]:
        paths = self.changed_paths(worktree)
        if not paths:
            return []
        result = self.runner.run(
            ["git", "add", "--", *paths],
            cwd=worktree.path,
            actor="workspace_manager",
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "git add failed")
        return paths

    def diff(self, worktree: Worktree, *, staged: bool = False) -> str:
        argv = ["git", "diff"]
        if staged:
            argv.append("--cached")
        argv.extend(["--no-ext-diff"])
        result = self.runner.run(argv, cwd=worktree.path, actor="workspace_manager")
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "git diff failed")
        return result.stdout

    def commit(self, worktree: Worktree, *, message: str) -> str:
        paths = self.stage(worktree)
        if not paths:
            current = self.runner.run(
                ["git", "show", "-s", "--format=%H", "HEAD"],
                cwd=worktree.path,
                actor="workspace_manager",
            )
            if current.returncode != 0:
                raise RuntimeError(current.stderr.strip() or "git show failed")
            return current.stdout.strip()
        result = self.runner.run(
            ["git", "commit", "-m", message],
            cwd=worktree.path,
            actor="workspace_manager",
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "git commit failed")
        current = self.runner.run(
            ["git", "show", "-s", "--format=%H", "HEAD"],
            cwd=worktree.path,
            actor="workspace_manager",
        )
        if current.returncode != 0:
            raise RuntimeError(current.stderr.strip() or "git show failed")
        return current.stdout.strip()

    def push(
        self,
        worktree: Worktree,
        *,
        approved_rules: set[str],
    ) -> None:
        result = self.runner.run(
            ["git", "push", "-u", "origin", worktree.branch],
            cwd=worktree.path,
            approved_rules=approved_rules,
            actor="workspace_manager",
            timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "git push failed")

    def cleanup(self, worktree: Worktree) -> None:
        self._require_under_root(worktree.path.resolve(), self.worktree_root)
        result = self.runner.run(
            ["git", "worktree", "remove", "--force", str(worktree.path)],
            cwd=self.source_root,
            actor="workspace_manager",
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "git worktree remove failed")
        self.audit.write(
            "level6.worktree.removed",
            actor="workspace_manager",
            data={"path": str(worktree.path), "branch": worktree.branch},
        )

    @staticmethod
    def _require_under_root(path: Path, root: Path) -> None:
        if path == root or root not in path.parents:
            raise WorkspaceBoundaryError(f"path escapes managed root: {path}")

    @staticmethod
    def _safe_relative(value: str) -> str:
        path = Path(value)
        if path.is_absolute() or ".." in path.parts:
            raise WorkspaceBoundaryError(f"unsafe git path: {value}")
        return path.as_posix()


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return slug[:72] or "work"
