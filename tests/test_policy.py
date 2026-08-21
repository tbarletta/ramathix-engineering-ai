from pathlib import Path

from rea.domain import Decision
from rea.policy import CommandPolicy


POLICY = Path(__file__).parents[1] / "config" / "policies" / "commands.yaml"


def test_known_safe_command_is_allowed() -> None:
    policy = CommandPolicy.from_yaml(POLICY)
    assert policy.evaluate(["git", "status"]).decision == Decision.ALLOW


def test_unknown_command_requires_approval() -> None:
    policy = CommandPolicy.from_yaml(POLICY)
    assert policy.evaluate(["node", "script.js"]).decision == Decision.ASK


def test_cost_command_requires_human_cost_approval() -> None:
    policy = CommandPolicy.from_yaml(POLICY)
    assert policy.evaluate(["terraform", "apply"]).decision == Decision.COST_APPROVAL


def test_destructive_command_is_denied() -> None:
    policy = CommandPolicy.from_yaml(POLICY)
    assert policy.evaluate(["sudo", "apt", "upgrade"]).decision == Decision.DENY
