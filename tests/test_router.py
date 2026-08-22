from pathlib import Path

from rea.models import ModelRouter

CONFIG = Path(__file__).parents[1] / "config" / "models.yaml"


def test_tech_lead_routes_to_reasoner() -> None:
    router = ModelRouter.from_yaml(CONFIG)
    target = router.resolve("tech_lead")
    assert target.alias == "reasoner"
    assert target.provider == "ollama"


def test_backend_routes_to_coder() -> None:
    router = ModelRouter.from_yaml(CONFIG)
    assert router.resolve("senior_backend").alias == "coder"
