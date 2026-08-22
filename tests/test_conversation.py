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
    assert '"repository": "social-media"' in system
    assert '"TypeScript": 42' in system
