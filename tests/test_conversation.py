from pathlib import Path

from rea.conversation import ConversationAssistant
from rea.models import ModelRouter

CONFIG = Path(__file__).parents[1] / "config" / "models.yaml"


class FakeConversationModel:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def chat(self, *, model: str, messages: list[dict[str, str]]) -> str:
        self.calls.append({"model": model, "messages": messages})
        return "Vamos planejar isso juntos."


def test_conversation_uses_reasoner_and_preserves_session_context() -> None:
    model = FakeConversationModel()
    assistant = ConversationAssistant(ModelRouter.from_yaml(CONFIG), model)

    assert assistant.reply("Quero melhorar a confiabilidade.") == "Vamos planejar isso juntos."
    assistant.reply("O repositório é tbarletta/social-media.")

    assert model.calls[0]["model"] == "qwen3:14b"
    second_messages = model.calls[1]["messages"]
    assert {"role": "user", "content": "Quero melhorar a confiabilidade."} in second_messages
    assert {"role": "assistant", "content": "Vamos planejar isso juntos."} in second_messages


def test_conversation_includes_repository_knowledge_in_system_prompt() -> None:
    model = FakeConversationModel()
    assistant = ConversationAssistant(
        ModelRouter.from_yaml(CONFIG),
        model,
        knowledge={"repository": "social-media", "languages": {"TypeScript": 42}},
    )

    assistant.reply("Quais linguagens o projeto usa?")

    system = model.calls[0]["messages"][0]["content"]
    assert "Always answer in Brazilian Portuguese" in system
    assert '"repository": "social-media"' in system
    assert '"TypeScript": 42' in system


def test_project_analysis_uses_deterministic_repository_dossier() -> None:
    model = FakeConversationModel()
    assistant = ConversationAssistant(
        ModelRouter.from_yaml(CONFIG),
        model,
        knowledge={
            "repository": "demo",
            "file_count": 12,
            "languages": {"Python": 10},
            "facts": [{"category": "framework", "name": "Typer", "evidence": "pyproject.toml"}],
            "architecture": {
                "package": {"name": "demo", "python": ">=3.11"},
                "components": [
                    {"path": "src/demo", "source_files": 3, "classes": 1, "functions": 4}
                ],
                "entrypoints": [{"name": "demo", "target": "demo.cli:app"}],
                "external_imports": [{"package": "typer", "uses": 3}],
                "local_import_edges": 5,
                "test_files": 2,
            },
        },
    )

    response = assistant.reply("Faça uma análise do projeto")

    assert "## Análise técnica: demo" in response
    assert "Typer" in response
    assert "src/demo" in response
    assert "3 arquivos de código, 1 classe e 4 funções" in response
    assert model.calls == []
