from __future__ import annotations

import shutil
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
    ruff = shutil.which("ruff")
    pytest = shutil.which("pytest")

    if ruff is None or pytest is None:
        print(
            "Ruff or Pytest was not found in the active Python environment. "
            "Install the project dev dependencies first: pip install -e '.[dev]'",
            file=sys.stderr,
        )
        return 2

    run(
        "Python bytecode compilation",
        [str(python), "-m", "compileall", "-q", "src", "tests"],
    )
    run("Dependency consistency", [str(python), "-m", "pip", "check"])
    run("Ruff", [ruff, "check", "."])
    run("Pytest", [pytest, "-q"])

    print("\nREA validation: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
