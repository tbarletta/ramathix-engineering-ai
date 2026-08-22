from pathlib import Path

from rea.cli import _initialize_current_repository
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


def test_bundled_runtime_configuration_stays_equal_to_checkout_defaults() -> None:
    root = Path(__file__).parents[1]
    resources = root / "src" / "rea" / "resources"

    assert (resources / "models.yaml").read_text(encoding="utf-8") == (
        root / "config" / "models.yaml"
    ).read_text(encoding="utf-8")
    assert (resources / "policies" / "commands.yaml").read_text(encoding="utf-8") == (
        root / "config" / "policies" / "commands.yaml"
    ).read_text(encoding="utf-8")
