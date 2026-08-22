from pathlib import Path

from rea.models import ModelRouter

CONFIG = Path(__file__).parents[1] / "config" / "models.yaml"


def test_tech_lead_routes_to_reasoner() -> None:
    router = ModelRouter.from_yaml(CONFIG)
    target = router.resolve("tech_lead")
    assert target.alias == "reasoner"
    assert target.provider == "ollama"
    assert target.model == "qwen3:14b"


def test_backend_routes_to_coder() -> None:
    router = ModelRouter.from_yaml(CONFIG)
    target = router.resolve("senior_backend")
    assert target.alias == "coder"
    assert target.model == "qwen3:14b"


def test_finops_routes_to_available_utility_model() -> None:
    router = ModelRouter.from_yaml(CONFIG)
    target = router.resolve("finops")
    assert target.alias == "utility"
    assert target.model == "qwen3:8b"
