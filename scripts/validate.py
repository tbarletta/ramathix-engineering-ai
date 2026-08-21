from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(label: str, argv: list[str]) -> None:
    print(f"\n== {label} ==")
    print("$ " + " ".join(argv))
    completed = subprocess.run(argv, cwd=ROOT, check=False)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def main() -> int:
    python = Path(sys.executable).resolve()
    executable_dir = python.parent
    ruff_name = "ruff.exe" if os.name == "nt" else "ruff"
    ruff = executable_dir / ruff_name

    if not ruff.exists():
        print(
            "Ruff was not found in the active Python environment. "
            "Install the project dev dependencies first: pip install -e '.[dev]'",
            file=sys.stderr,
        )
        return 2

    run(
        "Python bytecode compilation",
        [str(python), "-m", "compileall", "-q", "src", "tests"],
    )
    run("Dependency consistency", [str(python), "-m", "pip", "check"])
    run("Ruff", [str(ruff), "check", "."])
    run("Pytest", [str(python), "-m", "pytest", "-q"])

    print("\nREA validation: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
