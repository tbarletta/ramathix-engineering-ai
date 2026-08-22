from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from ..models import ModelRouter
from ..team.agents import StructuredModelClient
from ..team.context import TeamKnowledgeContext
from ..team.contracts import CodeReview, ReviewDecision, ReviewFinding
from .contracts import CodingIteration, FileMutation

CODING_ITERATION_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "changes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "action": {
                        "type": "string",
                        "enum": ["create", "modify", "delete"],
                    },
                    "content": {
                        "anyOf": [{"type": "string"}, {"type": "null"}],
                    },
                },
                "required": ["path", "action", "content"],
                "additionalProperties": False,
            },
        },
        "commands": {"type": "array", "items": {"type": "string"}},
        "cost_impact": {"type": "boolean"},
    },
    "required": ["summary", "changes", "commands", "cost_impact"],
    "additionalProperties": False,
}

SOURCE_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {
            "type": "string",
            "enum": ["approve", "request_changes"],
        },
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {
                        "type": "string",
                        "enum": ["info", "low", "medium", "high", "critical"],
                    },
                    "category": {
                        "type": "string",
                        "enum": [
                            "architecture",
                            "business_rule",
                            "security",
                            "performance",
                            "database",
                            "testing",
                            "operations",
                            "maintainability",
                        ],
                    },
                    "message": {"type": "string"},
                },
                "required": ["severity", "category", "message"],
                "additionalProperties": False,
            },
        },
        "missing_tests": {"type": "array", "items": {"type": "string"}},
        "risk_notes": {"type": "array", "items": {"type": "string"}},
        "cost_impact": {"type": "boolean"},
    },
    "required": [
        "decision",
        "summary",
        "findings",
        "missing_tests",
        "risk_notes",
        "cost_impact",
    ],
    "additionalProperties": False,
}


class MutationBoundaryError(RuntimeError):
    pass


class SecretDetected(RuntimeError):
    pass


_SENSITIVE_NAMES = {
    ".env",
    "id_rsa",
    "id_ed25519",
    "credentials.json",
    "service-account.json",
}
_SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
]
_ASSIGNMENT_SECRET = re.compile(
    r"(?i)(password|secret|token|api[_-]?key)\s*[:=]\s*['\"]([^'\"]{12,})['\"]"
)


def _sensitive_path(relative: str) -> bool:
    path = Path(relative)
    lowered = {part.lower() for part in path.parts}
    if path.name.lower() in _SENSITIVE_NAMES:
        return True
    if path.name.lower().startswith(".env."):
        return True
    if path.suffix.lower() in {".pem", ".p12", ".pfx"}:
        return True
    return bool(lowered & {".secrets", "secrets", "credentials"})


def _redact_source(content: str) -> str:
    redacted = content
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("***REDACTED_SECRET***", redacted)
    redacted = _ASSIGNMENT_SECRET.sub(
        lambda match: f'{match.group(1)} = "***REDACTED_SECRET***"',
        redacted,
    )
    return redacted


def _reject_secret_content(content: str) -> None:
    for pattern in _SECRET_PATTERNS:
        if pattern.search(content):
            raise SecretDetected("generated content contains a credential-like secret")
    for match in _ASSIGNMENT_SECRET.finditer(content):
        value = match.group(2).lower()
        if not any(marker in value for marker in ("example", "placeholder", "changeme", "${", "<")):
            raise SecretDetected("generated content contains a hard-coded secret-like value")


class WorkspaceReader:
    def __init__(
        self,
        workspace: Path,
        *,
        max_file_bytes: int = 80_000,
        max_total_bytes: int = 320_000,
    ) -> None:
        self.workspace = workspace.resolve()
        self.max_file_bytes = max_file_bytes
        self.max_total_bytes = max_total_bytes

    def snapshot(self, paths: set[str]) -> dict[str, str]:
        payload: dict[str, str] = {}
        total = 0
        for relative in sorted(paths):
            if _sensitive_path(relative):
                payload[relative] = "<SENSITIVE_PATH_REDACTED>"
                continue
            target = self._resolve(relative)
            if not target.exists():
                payload[relative] = "<MISSING>"
                continue
            if target.is_symlink() or not target.is_file():
                payload[relative] = "<UNAVAILABLE>"
                continue
            size = target.stat().st_size
            if size > self.max_file_bytes or total + size > self.max_total_bytes:
                payload[relative] = "<TOO_LARGE>"
                continue
            payload[relative] = _redact_source(target.read_text(encoding="utf-8"))
            total += size
        return payload

    def _resolve(self, relative: str) -> Path:
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            raise MutationBoundaryError(f"unsafe source path: {relative}")
        target = (self.workspace / path).resolve(strict=False)
        if target != self.workspace and self.workspace not in target.parents:
            raise MutationBoundaryError(f"path escapes workspace: {relative}")
        return target


class WorkspaceMutator:
    def __init__(
        self,
        workspace: Path,
        *,
        allowed_paths: set[str],
        max_content_bytes: int = 1_000_000,
    ) -> None:
        self.workspace = workspace.resolve()
        self.allowed_paths = {Path(path).as_posix() for path in allowed_paths}
        self.max_content_bytes = max_content_bytes

    def apply(self, mutation: FileMutation) -> None:
        relative = Path(mutation.path).as_posix()
        if _sensitive_path(relative):
            raise MutationBoundaryError(f"refusing to mutate sensitive path: {relative}")
        if relative not in self.allowed_paths:
            raise MutationBoundaryError(f"path was not approved by the plan: {relative}")
        target = self._resolve(relative)
        if target.is_symlink():
            raise MutationBoundaryError(f"refusing to mutate symlink: {relative}")

        if mutation.action == "delete":
            if mutation.content is not None:
                raise ValueError("delete mutation must use null content")
            if target.exists():
                if not target.is_file():
                    raise MutationBoundaryError(f"refusing to delete non-file: {relative}")
                target.unlink()
            return

        if mutation.content is None:
            raise ValueError(f"{mutation.action} mutation requires content")
        _reject_secret_content(mutation.content)
        encoded = mutation.content.encode("utf-8")
        if len(encoded) > self.max_content_bytes:
            raise ValueError(f"mutation content too large: {relative}")
        if mutation.action == "create" and target.exists():
            raise FileExistsError(relative)
        if mutation.action == "modify" and not target.exists():
            raise FileNotFoundError(relative)

        target.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(target, mutation.content)

    def _resolve(self, relative: str) -> Path:
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            raise MutationBoundaryError(f"unsafe mutation path: {relative}")
        target = (self.workspace / path).resolve(strict=False)
        if target != self.workspace and self.workspace not in target.parents:
            raise MutationBoundaryError(f"path escapes workspace: {relative}")
        return target

    @staticmethod
    def _atomic_write(target: Path, content: str) -> None:
        fd, temporary = tempfile.mkstemp(
            prefix=f".{target.name}.",
            suffix=".rea-tmp",
            dir=target.parent,
            text=True,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(content)
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


class SeniorDeveloperExecutionAgent:
    role = "senior_developer"

    def __init__(self, router: ModelRouter, model: StructuredModelClient) -> None:
        self.router = router
        self.model = model

    def implement(
        self,
        *,
        work_package: dict[str, Any],
        context: TeamKnowledgeContext,
        source_files: dict[str, str],
        feedback: list[str],
    ) -> CodingIteration:
        target = self.router.resolve(self.role)
        system = (
            "You are the executing Senior Developer. Produce concrete file mutations for the "
            "approved technical plan. For create/modify return the complete UTF-8 file content; "
            "for delete return null content. You may only change paths supplied in source_files. "
            "Preserve architecture and business constraints. Use feedback from tests/review to "
            "correct the current implementation. Commands must be validation/build/test commands "
            "only; do not propose git push, cloud provisioning, deployment or destructive actions. "
            "Never include secrets. Mark cost_impact true if any implementation may create or "
            "increase monetary cost."
        )
        user = json.dumps(
            {
                "work_package": work_package,
                "knowledge": context.compact,
                "source_files": source_files,
                "feedback": feedback,
            },
            ensure_ascii=False,
            indent=2,
        )
        data = self.model.chat_json(
            model=target.model,
            system=system,
            user=user,
            schema=CODING_ITERATION_SCHEMA,
        )
        return CodingIteration(
            summary=data["summary"],
            changes=[
                FileMutation(
                    path=item["path"],
                    action=item["action"],
                    content=item["content"],
                )
                for item in data["changes"]
            ],
            commands=list(data["commands"]),
            cost_impact=bool(data["cost_impact"]),
            raw=data,
        )


class SourceCodeReviewerAgent:
    role = "code_review"

    def __init__(self, router: ModelRouter, model: StructuredModelClient) -> None:
        self.router = router
        self.model = model

    def review(
        self,
        *,
        work_package: dict[str, Any],
        context: TeamKnowledgeContext,
        diff: str,
        validation: list[dict[str, Any]],
    ) -> CodeReview:
        target = self.router.resolve(self.role)
        system = (
            "You are an independent Senior Code Reviewer. Review the actual staged diff and "
            "validation results, plus the approved plan. Request changes for material "
            "architecture, business-rule, "
            "security, performance, database, testing, operations or maintainability gaps. Never "
            "approve a diff that introduces undeclared monetary cost or contradicts "
            "higher-priority knowledge. Do not request unrelated refactors."
        )
        user = json.dumps(
            {
                "work_package": work_package,
                "knowledge": context.compact,
                "diff": diff[:160_000],
                "validation": validation,
            },
            ensure_ascii=False,
            indent=2,
        )
        data = self.model.chat_json(
            model=target.model,
            system=system,
            user=user,
            schema=SOURCE_REVIEW_SCHEMA,
        )
        return CodeReview(
            decision=ReviewDecision(data["decision"]),
            summary=data["summary"],
            findings=[
                ReviewFinding(
                    severity=item["severity"],
                    category=item["category"],
                    message=item["message"],
                )
                for item in data["findings"]
            ],
            missing_tests=list(data["missing_tests"]),
            risk_notes=list(data["risk_notes"]),
            cost_impact=bool(data["cost_impact"]),
            raw=data,
        )
