from pathlib import Path
from types import SimpleNamespace

from rea.models import ModelRouter, OllamaClient

CONFIG = Path(__file__).parents[1] / "config" / "models.yaml"


def test_tech_lead_routes_to_reasoner() -> None:
    router = ModelRouter.from_yaml(CONFIG)
    target = router.resolve("tech_lead")
    assert target.alias == "reasoner"
    assert target.provider == "ollama"
    assert target.model == "gpt-oss:20b"


def test_backend_routes_to_coder() -> None:
    router = ModelRouter.from_yaml(CONFIG)
    target = router.resolve("senior_backend")
    assert target.alias == "coder"
    assert target.model == "gpt-oss:20b"


def test_finops_routes_to_available_utility_model() -> None:
    router = ModelRouter.from_yaml(CONFIG)
    target = router.resolve("finops")
    assert target.alias == "utility"
    assert target.model == "gpt-oss:20b"


def test_ollama_chat_returns_assistant_content(monkeypatch) -> None:
    captured = {}

    def fake_post(url, *, json, timeout):
        captured.update(url=url, json=json, timeout=timeout)
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"message": {"content": "Olá"}},
        )

    monkeypatch.setattr("rea.models.httpx.post", fake_post)

    response = OllamaClient("http://ollama.test").chat(
        model="qwen3:14b",
        messages=[{"role": "user", "content": "Olá"}],
    )

    assert response == "Olá"
    assert captured["url"] == "http://ollama.test/api/chat"
    assert captured["json"]["stream"] is False
    assert captured["json"]["think"] is False
    assert captured["json"]["options"] == {"num_predict": 384}
