from pathlib import Path

from rea.conversation import ConversationAssistant
from rea.models import ModelRouter

CONFIG = Path(__file__).parents[1] / "config" / "models.yaml"


class FakeConversationModel:
    def __init__(self, reply: str = "Vamos planejar isso juntos.") -> None:
        self.calls: list[dict[str, object]] = []
        self.reply_text = reply

    def chat_stream(self, *, model: str, messages: list[dict[str, str]], max_tokens: int = 384):
        self.calls.append({"model": model, "messages": messages, "max_tokens": max_tokens})
        for word in self.reply_text.split(" "):
            yield word + " "


def test_conversation_uses_reasoner_and_preserves_session_context() -> None:
    model = FakeConversationModel()
    assistant = ConversationAssistant(ModelRouter.from_yaml(CONFIG), model)

    assert assistant.reply("Quero melhorar a confiabilidade.") == "Vamos planejar isso juntos."
    assistant.reply("O repositório é tbarletta/social-media.")

    assert model.calls[0]["model"] == "gpt-oss:20b"
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


def test_reply_streams_redacted_chunks_via_on_token() -> None:
    long_reply = "Primeira linha da resposta.\nSegunda linha um pouco mais longa.\n" * 4
    model = FakeConversationModel(reply=long_reply.strip())
    assistant = ConversationAssistant(ModelRouter.from_yaml(CONFIG), model)
    received: list[str] = []

    result = assistant.reply("Oi", on_token=received.append)

    assert result == long_reply.strip()
    assert "".join(received).strip() == result
    assert len(received) > 1


def test_conversation_remembers_governed_controller_outcomes() -> None:
    model = FakeConversationModel()
    assistant = ConversationAssistant(ModelRouter.from_yaml(CONFIG), model)

    assistant.remember("/aprovar", "Issues publicadas: WU-001 → #42")
    assistant.reply("Qual foi o resultado da ação?")

    messages = model.calls[0]["messages"]
    assert {"role": "assistant", "content": "Issues publicadas: WU-001 → #42"} in messages
