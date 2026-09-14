import json
from pathlib import Path

import pytest

from rea.evolution.skills import (
    SkillGapDetector,
    SkillLifecycle,
    SkillStatus,
    SkillValidator,
)


class Runner:
    def __init__(self, passed=True):
        self.passed = passed
        self.calls = 0

    def run(self, package, commands, timeout_seconds):
        self.calls += 1
        return self.passed, ("ok",)


def package(
    root: Path,
    version: str = "1.0.0",
    *,
    permissions=(),
    dependencies=(),
    minimum_observations=2,
):
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text("# Skill de teste\n", encoding="utf-8")
    (root / "main.py").write_text("def run():\n    return True\n", encoding="utf-8")
    (root / "skill.json").write_text(
        json.dumps(
            {
                "id": "skill-teste",
                "name": "Skill Teste",
                "version": version,
                "description": "Valida o ciclo completo.",
                "entrypoint": "main.py",
                "permissions": list(permissions),
                "dependencies": list(dependencies),
                "validation_commands": [["python", "-m", "compileall", "."]],
                "minimum_success_rate": 0.8,
                "minimum_observations": minimum_observations,
                "max_latency_seconds": 10,
            }
        ),
        encoding="utf-8",
    )
    return root


def lifecycle(tmp_path, passed=True):
    return SkillLifecycle(
        tmp_path / "managed",
        SkillValidator(Runner(passed)),
    )


def test_detector_requires_repeated_skill_gap():
    events = [
        {
            "id": f"e-{number}",
            "event": "intent.unsupported",
            "data": {"missing_capability": "gerar-diagrama"},
        }
        for number in range(2)
    ]
    proposals = SkillGapDetector(2).detect(events)
    assert [item.id for item in proposals] == ["skill-gerar-diagrama"]


def test_install_validates_registers_and_activates(tmp_path):
    service = lifecycle(tmp_path)
    record = service.install(package(tmp_path / "candidate"))
    assert record.status is SkillStatus.ACTIVE
    assert Path(record.package_path).is_dir()
    assert service.registry.get("skill-teste").version == "1.0.0"


def test_validation_failure_prevents_registration(tmp_path):
    service = lifecycle(tmp_path, passed=False)
    with pytest.raises(ValueError, match="validação funcional"):
        service.install(package(tmp_path / "candidate"))
    assert service.registry.load() == {}


def test_dangerous_source_is_rejected_before_sandbox(tmp_path):
    runner = Runner()
    candidate = package(tmp_path / "candidate")
    (candidate / "main.py").write_text(
        "import os\nos.system('echo unsafe')\n",
        encoding="utf-8",
    )
    report = SkillValidator(runner).validate(candidate)
    assert not report.allowed
    assert runner.calls == 0


def test_protected_permission_requires_explicit_approval(tmp_path):
    service = lifecycle(tmp_path)
    candidate = package(
        tmp_path / "candidate",
        permissions=("network.external",),
    )
    with pytest.raises(PermissionError, match="network.external"):
        service.install(candidate)
    record = service.activate(
        "skill-teste",
        "1.0.0",
        approvals={"network.external"},
    )
    assert record.status is SkillStatus.ACTIVE


def test_new_version_keeps_rollback_pointer(tmp_path):
    service = lifecycle(tmp_path)
    service.install(package(tmp_path / "v1", "1.0.0"))
    record = service.install(package(tmp_path / "v2", "1.1.0"))
    assert record.previous_version == "1.0.0"
    service.rollback("skill-teste", "regressão")
    assert service.registry.get("skill-teste").version == "1.0.0"


def test_metrics_trigger_automatic_rollback(tmp_path):
    service = lifecycle(tmp_path)
    service.install(package(tmp_path / "v1", "1.0.0"))
    service.install(package(tmp_path / "v2", "1.1.0"))
    service.record("skill-teste", success=False, latency_seconds=1)
    service.record("skill-teste", success=False, latency_seconds=1)
    changed = service.reconcile()
    assert changed[0].status is SkillStatus.ROLLED_BACK
    assert service.registry.get("skill-teste").version == "1.0.0"


def test_safety_failure_rolls_back_immediately(tmp_path):
    service = lifecycle(tmp_path)
    service.install(package(tmp_path / "v1", "1.0.0"))
    service.install(package(tmp_path / "v2", "1.1.0"))
    service.record(
        "skill-teste",
        success=True,
        latency_seconds=1,
        safety_failure=True,
    )
    service.reconcile()
    assert service.registry.get("skill-teste").version == "1.0.0"


def test_integrity_violation_quarantines_active_skill(tmp_path):
    service = lifecycle(tmp_path)
    record = service.install(package(tmp_path / "candidate"))
    (Path(record.package_path) / "main.py").write_text(
        "def run(): return False\n",
        encoding="utf-8",
    )
    changed = service.reconcile()
    assert changed[0].status is SkillStatus.QUARANTINED


def test_missing_dependency_is_rejected(tmp_path):
    service = lifecycle(tmp_path)
    with pytest.raises(ValueError, match="dependências ativas ausentes"):
        service.install(
            package(
                tmp_path / "candidate",
                dependencies=("skill-ausente",),
            )
        )
