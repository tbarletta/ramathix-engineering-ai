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


def test_shell_interpretation_is_denied_even_when_unknown() -> None:
    policy = CommandPolicy.from_yaml(POLICY)
    result = policy.evaluate(["bash", "-c", "git status"])
    assert result.decision is Decision.DENY
    assert result.rule_id == "semantic-shell"


def test_destructive_git_flags_are_denied_before_prefix_allowlist() -> None:
    policy = CommandPolicy.from_yaml(POLICY)
    result = policy.evaluate(["git", "checkout", "--force", "main"])
    assert result.decision is Decision.DENY
    assert result.rule_id == "semantic-git-destructive"


def test_docker_host_escape_is_denied() -> None:
    policy = CommandPolicy.from_yaml(POLICY)
    result = policy.evaluate(["docker", "run", "--privileged", "image"])
    assert result.decision is Decision.DENY
