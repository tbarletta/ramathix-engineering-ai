from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .models import Confidence, DependencyEdge, Evidence, EvidenceKind, SourceSymbol


@dataclass
class AstResult:
    symbols: list[SourceSymbol] = field(default_factory=list)
    dependencies: list[DependencyEdge] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class AstProvider(Protocol):
    def supports(self, path: Path) -> bool: ...
    def analyze(self, root: Path, path: Path) -> AstResult: ...


class PythonAstProvider:
    def supports(self, path: Path) -> bool:
        return path.suffix == ".py"

    def analyze(self, root: Path, path: Path) -> AstResult:
        relative = path.relative_to(root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError) as exc:
            return AstResult(warnings=[f"AST parse failed for {relative}: {exc}"])

        result = AstResult()
        for node in ast.walk(tree):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = "class" if isinstance(node, ast.ClassDef) else "function"
                result.symbols.append(
                    SourceSymbol(relative, node.name, kind, "Python", getattr(node, "lineno", 1))
                )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    result.dependencies.append(_import_edge(relative, alias.name))
            elif isinstance(node, ast.ImportFrom):
                target = "." * node.level + (node.module or "")
                if target:
                    result.dependencies.append(_import_edge(relative, target))
        return result


def _import_edge(source: str, target: str) -> DependencyEdge:
    return DependencyEdge(
        source=source,
        target=target,
        relation="imports",
        confidence=Confidence.CONFIRMED,
        evidence=Evidence(source, EvidenceKind.SOURCE, "parsed import"),
    )


class TypeScriptImportProvider:
    """Deterministic import graph fallback until a Tree-sitter grammar is configured."""

    IMPORT_RE = re.compile(
        r"(?:import\s+(?:[^;]*?\s+from\s+)?|require\s*\()"
        r"[\"'](?P<target>[^\"']+)[\"']"
    )

    def supports(self, path: Path) -> bool:
        return path.suffix in {".ts", ".tsx", ".js", ".jsx"}

    def analyze(self, root: Path, path: Path) -> AstResult:
        relative = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        result = AstResult()
        for line_number, line in enumerate(text.splitlines(), start=1):
            for match in self.IMPORT_RE.finditer(line):
                result.dependencies.append(
                    DependencyEdge(
                        source=relative,
                        target=match.group("target"),
                        relation="imports",
                        confidence=Confidence.CONFIRMED,
                        evidence=Evidence(
                            relative,
                            EvidenceKind.SOURCE,
                            f"import at line {line_number}",
                        ),
                    )
                )
        return result


class TreeSitterProvider:
    """Extension point for language grammars without coupling scanner core to Tree-sitter."""

    def __init__(self, language_suffixes: set[str]) -> None:
        self.language_suffixes = language_suffixes

    def supports(self, path: Path) -> bool:
        return path.suffix in self.language_suffixes

    def analyze(self, root: Path, path: Path) -> AstResult:
        relative = path.relative_to(root).as_posix()
        return AstResult(
            warnings=[
                f"Tree-sitter grammar not configured for {relative}; deterministic analyzers used"
            ]
        )
