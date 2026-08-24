from pathlib import Path

import pytest

from rea.cli import (
    _initialize_current_repository,
    _list_directory_action,
    _read_file_action,
    _resolve_workspace_path,
    _WorkspaceState,
)
from rea.config import Settings


def _clear_runtime_environment(monkeypatch) -> None:
    for name in (
        "REA_HOME",
        "REA_AUDIT_PATH",
        "REA_COMMAND_POLICY",
        "REA_MODEL_CONFIG",
        "REA_KNOWLEDGE_PATH",
        "REA_WORK_PATH",
        "REA_WORKTREE_PATH",
    ):
        monkeypatch.delenv(name, raising=False)


def test_global_cli_uses_its_own_configuration_from_another_project(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _clear_runtime_environment(monkeypatch)
    monkeypatch.chdir(tmp_path)

    settings = Settings.from_env()

    assert settings.home == tmp_path
    assert settings.model_config.is_file()
    assert settings.command_policy.is_file()
    assert settings.knowledge_path == tmp_path / ".rea" / "knowledge"


def test_conversation_initializes_code_inventory_without_git_repository(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _clear_runtime_environment(monkeypatch)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")

    knowledge = _initialize_current_repository(Settings.from_env(), tmp_path)

    assert knowledge is not None
    assert knowledge["repository"] == tmp_path.name
    assert knowledge["file_count"] == 1
    assert "sem histórico Git" in capsys.readouterr().out


def test_resolve_workspace_path_rejects_paths_that_escape_the_root(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError):
        _resolve_workspace_path(tmp_path, "../outside.txt")
    with pytest.raises(RuntimeError):
        _resolve_workspace_path(tmp_path, "../../etc/passwd")


def test_resolve_workspace_path_accepts_nested_paths_inside_the_root(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    resolved = _resolve_workspace_path(tmp_path, "src/app.py")
    assert resolved == (tmp_path / "src" / "app.py").resolve()


def test_read_file_action_returns_redacted_content(tmp_path: Path) -> None:
    (tmp_path / "config.txt").write_text("token: ghp_" + "a" * 30, encoding="utf-8")
    workspace = _WorkspaceState(tmp_path)

    content = _read_file_action(workspace, "config.txt")

    assert "***REDACTED_SECRET***" in content


def test_read_file_action_rejects_missing_files(tmp_path: Path) -> None:
    workspace = _WorkspaceState(tmp_path)
    with pytest.raises(RuntimeError):
        _read_file_action(workspace, "does-not-exist.txt")


def test_list_directory_action_lists_entries_with_trailing_slash_for_dirs(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "README.md").write_text("hi", encoding="utf-8")
    workspace = _WorkspaceState(tmp_path)

    listing = _list_directory_action(workspace, ".")

    assert "src/" in listing
    assert "README.md" in listing


def test_bundled_runtime_configuration_stays_equal_to_checkout_defaults() -> None:
    root = Path(__file__).parents[1]
    resources = root / "src" / "rea" / "resources"

    assert (resources / "models.yaml").read_text(encoding="utf-8") == (
        root / "config" / "models.yaml"
    ).read_text(encoding="utf-8")
    assert (resources / "policies" / "commands.yaml").read_text(encoding="utf-8") == (
        root / "config" / "policies" / "commands.yaml"
    ).read_text(encoding="utf-8")
