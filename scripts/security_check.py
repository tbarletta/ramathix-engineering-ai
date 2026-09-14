from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path


DANGEROUS_CALLS = {"eval", "exec", "os.system"}


def call_name(node: ast.Call) -> str:
    function = node.func
    if isinstance(function, ast.Name):
        return function.id
    if isinstance(function, ast.Attribute) and isinstance(function.value, ast.Name):
        return f"{function.value.id}.{function.attr}"
    return ""


def scan(root: Path) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for path in sorted((root / "src").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text("utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:
            findings.append(
                {"path": str(path.relative_to(root)), "line": 0, "kind": type(exc).__name__}
            )
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = call_name(node)
            shell_enabled = name in {"subprocess.run", "subprocess.Popen"} and any(
                keyword.arg == "shell"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value is True
                for keyword in node.keywords
            )
            if name in DANGEROUS_CALLS or shell_enabled:
                findings.append(
                    {
                        "path": str(path.relative_to(root)),
                        "line": node.lineno,
                        "kind": name or "subprocess.shell",
                    }
                )
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    findings = scan(root)
    Path(args.output).write_text(
        json.dumps(
            {"safety_failures": len(findings), "findings": findings},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
