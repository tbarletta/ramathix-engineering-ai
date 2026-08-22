from __future__ import annotations

import sys
import tomllib
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .models import RepositoryInventory

SOURCE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".kt"}


def summarize_architecture(
    root: Path,
    files: list[Path],
    inventory: RepositoryInventory,
) -> dict[str, Any]:
    components: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"source_files": 0, "classes": 0, "functions": 0}
    )
    for path in files:
        if path.suffix.lower() in SOURCE_SUFFIXES:
            components[_component_path(path.relative_to(root))]["source_files"] += 1
    for symbol in inventory.symbols:
        component = _component_path(Path(symbol.path))
        components[component]["classes" if symbol.kind == "class" else "functions"] += 1

    external_imports: Counter[str] = Counter()
    local_imports = 0
    stdlib = sys.stdlib_module_names
    local_packages = _local_package_names(files, root)
    for edge in inventory.dependencies:
        target = edge.target
        if target.startswith("."):
            local_imports += 1
            continue
        top_level = target.split(".", maxsplit=1)[0]
        if top_level in local_packages:
            local_imports += 1
            continue
        if top_level not in stdlib:
            external_imports[top_level] += 1

    return {
        "package": _package_metadata(files, root),
        "entrypoints": _entrypoints(files, root),
        "components": [
            {"path": path, **counts}
            for path, counts in sorted(components.items())
        ],
        "external_imports": [
            {"package": package, "uses": uses}
            for package, uses in external_imports.most_common(20)
        ],
        "local_import_edges": local_imports,
        "test_files": sum(_is_test_file(path) for path in files),
    }


def _component_path(relative: Path) -> str:
    parts = relative.parts
    if not parts:
        return "."
    if parts[0] == "src":
        return "/".join(parts[:3] if len(parts) >= 4 else parts[:2])
    if parts[0] in {"apps", "packages", "services"}:
        return "/".join(parts[:2])
    return parts[0] if len(parts) > 1 else "."


def _package_metadata(files: list[Path], root: Path) -> dict[str, str]:
    for path in files:
        if path.name != "pyproject.toml":
            continue
        try:
            project = tomllib.loads(path.read_text(encoding="utf-8", errors="replace")).get(
                "project", {}
            )
        except tomllib.TOMLDecodeError:
            continue
        return {
            "name": str(project.get("name", root.name)),
            "description": str(project.get("description", "")),
            "python": str(project.get("requires-python", "")),
        }
    return {"name": root.name}


def _entrypoints(files: list[Path], root: Path) -> list[dict[str, str]]:
    for path in files:
        if path.name != "pyproject.toml":
            continue
        try:
            project = tomllib.loads(path.read_text(encoding="utf-8", errors="replace")).get(
                "project", {}
            )
        except tomllib.TOMLDecodeError:
            continue
        return [
            {"name": str(name), "target": str(target)}
            for name, target in sorted((project.get("scripts") or {}).items())
        ]
    return []


def _local_package_names(files: list[Path], root: Path) -> set[str]:
    packages: set[str] = set()
    for path in files:
        if path.name != "__init__.py":
            continue
        parts = path.relative_to(root).parts
        if "src" in parts:
            source_index = parts.index("src")
            if source_index + 1 < len(parts):
                packages.add(parts[source_index + 1])
        elif len(parts) > 1:
            packages.add(parts[0])
    return packages


def _is_test_file(path: Path) -> bool:
    name = path.name.lower()
    return (
        name.startswith("test_")
        or name.endswith("_test.py")
        or ".spec." in name
        or ".test." in name
    )
