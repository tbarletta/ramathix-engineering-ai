from __future__ import annotations

from collections import Counter
from pathlib import Path

from ..audit import AuditLog
from ..execution import GovernedLocalRunner
from ..policy import CommandPolicy
from .analyzers import (
    ApiAnalyzer,
    DataAnalyzer,
    FrameworkAnalyzer,
    InfrastructureAnalyzer,
    ManifestAnalyzer,
    MessagingAnalyzer,
    RepositoryAnalyzer,
    ScanContext,
    TestAnalyzer,
    unique_facts,
)
from .architecture import summarize_architecture
from .ast import AstProvider, PythonAstProvider, TypeScriptImportProvider
from .git import GitHistoryAnalyzer
from .models import RepositoryInventory

LANGUAGES = {
    ".py": "Python",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".java": "Java",
    ".go": "Go",
    ".rs": "Rust",
    ".cs": "C#",
    ".swift": "Swift",
    ".rb": "Ruby",
    ".php": "PHP",
    ".sql": "SQL",
    ".sh": "Shell",
}

DEFAULT_IGNORES = {
    ".git",
    ".idea",
    ".vscode",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    ".gradle",
    ".next",
    "coverage",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}


class RepositoryScanner:
    def __init__(
        self,
        *,
        policy: CommandPolicy,
        audit: AuditLog,
        analyzers: list[RepositoryAnalyzer] | None = None,
        ast_providers: list[AstProvider] | None = None,
        max_files: int = 10_000,
        max_read_bytes: int = 512_000,
    ) -> None:
        self.policy = policy
        self.audit = audit
        self.analyzers = analyzers or [
            ManifestAnalyzer(),
            FrameworkAnalyzer(),
            DataAnalyzer(),
            MessagingAnalyzer(),
            InfrastructureAnalyzer(),
            TestAnalyzer(),
            ApiAnalyzer(),
        ]
        self.ast_providers = ast_providers or [PythonAstProvider(), TypeScriptImportProvider()]
        self.max_files = max_files
        self.max_read_bytes = max_read_bytes

    def scan(self, root: Path, *, include_git: bool = True) -> RepositoryInventory:
        root = root.resolve()
        if not root.is_dir():
            raise ValueError(f"repository path is not a directory: {root}")

        files = self._collect_files(root)
        inventory = RepositoryInventory(name=root.name, root=str(root), file_count=len(files))
        inventory.languages = dict(sorted(self._languages(files).items()))
        context = ScanContext(root=root, files=files, max_read_bytes=self.max_read_bytes)

        for analyzer in self.analyzers:
            result = analyzer.analyze(context)
            inventory.facts.extend(result.facts)
            inventory.dependencies.extend(result.dependencies)
            inventory.manifests.extend(result.manifests)
            inventory.warnings.extend(result.warnings)

        for path in files:
            if path.stat().st_size > self.max_read_bytes:
                continue
            for provider in self.ast_providers:
                if provider.supports(path):
                    result = provider.analyze(root, path)
                    inventory.symbols.extend(result.symbols)
                    inventory.dependencies.extend(result.dependencies)
                    inventory.warnings.extend(result.warnings)
                    break

        inventory.facts = unique_facts(inventory.facts)
        inventory.manifests = sorted(set(inventory.manifests))
        inventory.dependencies = _unique_dependencies(inventory.dependencies)
        inventory.symbols.sort(key=lambda item: (item.path, item.line, item.name))
        inventory.warnings = sorted(set(inventory.warnings))
        inventory.architecture = summarize_architecture(root, files, inventory)

        if include_git and (root / ".git").exists():
            inventory.git = GitHistoryAnalyzer(
                GovernedLocalRunner(self.policy, self.audit)
            ).summarize(root)

        self.audit.write(
            "repository.scanned",
            actor="knowledge_engine",
            data={
                "root": str(root),
                "files": inventory.file_count,
                "facts": len(inventory.facts),
                "dependencies": len(inventory.dependencies),
                "symbols": len(inventory.symbols),
            },
        )
        return inventory

    def _collect_files(self, root: Path) -> list[Path]:
        files: list[Path] = []
        for path in root.rglob("*"):
            if any(part in DEFAULT_IGNORES for part in path.relative_to(root).parts):
                continue
            if path.is_symlink():
                continue
            if path.is_file():
                files.append(path)
                if len(files) >= self.max_files:
                    break
        return sorted(files)

    def _languages(self, files: list[Path]) -> Counter[str]:
        counts: Counter[str] = Counter()
        for path in files:
            language = LANGUAGES.get(path.suffix.lower())
            if language:
                counts[language] += 1
        return counts


def _unique_dependencies(dependencies):
    unique = {}
    for dependency in dependencies:
        key = (dependency.source, dependency.target, dependency.relation)
        unique[key] = dependency
    return sorted(unique.values(), key=lambda item: (item.source, item.relation, item.target))
