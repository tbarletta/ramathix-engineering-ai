from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Protocol
from uuid import uuid4


_ID = re.compile(r"^[a-z][a-z0-9-]{2,63}$")
_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
_DANGEROUS = (
    re.compile(r"\brm\s+-rf\b", re.IGNORECASE),
    re.compile(r"\bos\.system\s*\("),
    re.compile(r"\bshell\s*=\s*True\b"),
    re.compile(r"\b(eval|exec)\s*\("),
    re.compile(r"(\.ssh|\.aws|credentials|private[_-]?key)", re.IGNORECASE),
)
_PROTECTED = frozenset(
    {
        "credentials.read",
        "credentials.write",
        "data.delete",
        "github.merge",
        "governance.write",
        "network.external",
        "production.write",
        "skill.manage",
    }
)


class SkillStatus(StrEnum):
    CANDIDATE = "candidate"
    VALIDATED = "validated"
    ACTIVE = "active"
    QUARANTINED = "quarantined"
    SUPERSEDED = "superseded"
    ROLLED_BACK = "rolled_back"
    RETIRED = "retired"


@dataclass(frozen=True)
class SkillProposal:
    id: str
    name: str
    description: str
    evidence: tuple[str, ...]
    required_capabilities: tuple[str, ...] = ()
    acceptance_criteria: tuple[str, ...] = ()
    risk: str = "low"

    @classmethod
    def from_gap(cls, capability: str, evidence: list[str]) -> SkillProposal:
        slug = re.sub(r"[^a-z0-9]+", "-", capability.lower()).strip("-")
        slug = slug[:52] or "unknown-capability"
        return cls(
            id=f"skill-{slug}",
            name=capability.replace("_", " ").replace("-", " ").title(),
            description=f"Atende automaticamente à capacidade ausente: {capability}.",
            evidence=tuple(sorted(evidence)),
            acceptance_criteria=(f"Executar {capability} com resultado verificável",),
        )


@dataclass(frozen=True)
class SkillManifest:
    id: str
    name: str
    version: str
    description: str
    entrypoint: str
    permissions: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    validation_commands: tuple[tuple[str, ...], ...] = ()
    minimum_success_rate: float = 0.8
    minimum_observations: int = 5
    max_latency_seconds: float = 120.0

    @classmethod
    def load(cls, package: Path) -> SkillManifest:
        path = package / "skill.json"
        if not path.is_file():
            raise ValueError("skill.json ausente")
        raw = json.loads(path.read_text("utf-8"))
        commands = tuple(tuple(str(part) for part in command) for command in raw.get(
            "validation_commands", []
        ))
        manifest = cls(
            id=str(raw["id"]),
            name=str(raw["name"]),
            version=str(raw["version"]),
            description=str(raw["description"]),
            entrypoint=str(raw["entrypoint"]),
            permissions=tuple(sorted(set(raw.get("permissions", [])))),
            dependencies=tuple(sorted(set(raw.get("dependencies", [])))),
            validation_commands=commands,
            minimum_success_rate=float(raw.get("minimum_success_rate", 0.8)),
            minimum_observations=int(raw.get("minimum_observations", 5)),
            max_latency_seconds=float(raw.get("max_latency_seconds", 120)),
        )
        manifest.validate()
        return manifest

    def validate(self) -> None:
        if not _ID.fullmatch(self.id):
            raise ValueError("id de skill inválido")
        if not _VERSION.fullmatch(self.version):
            raise ValueError("versão deve seguir SemVer x.y.z")
        if not self.name.strip() or not self.description.strip():
            raise ValueError("nome e descrição são obrigatórios")
        entry = Path(self.entrypoint)
        if entry.is_absolute() or ".." in entry.parts:
            raise ValueError("entrypoint deve permanecer dentro do pacote")
        if not 0 <= self.minimum_success_rate <= 1:
            raise ValueError("minimum_success_rate inválido")
        if self.minimum_observations < 1 or self.max_latency_seconds <= 0:
            raise ValueError("limites operacionais inválidos")
        if not self.validation_commands:
            raise ValueError("ao menos um comando de validação é obrigatório")
        for command in self.validation_commands:
            if not command or any(not part for part in command):
                raise ValueError("comando de validação inválido")


@dataclass
class SkillMetrics:
    invocations: int = 0
    successes: int = 0
    failures: int = 0
    safety_failures: int = 0
    total_latency_seconds: float = 0.0
    total_cost: float = 0.0

    @property
    def success_rate(self) -> float:
        return self.successes / self.invocations if self.invocations else 0.0

    @property
    def average_latency_seconds(self) -> float:
        return (
            self.total_latency_seconds / self.invocations
            if self.invocations
            else 0.0
        )


@dataclass
class SkillRecord:
    id: str
    version: str
    digest: str
    package_path: str
    status: SkillStatus
    permissions: list[str]
    dependencies: list[str]
    created_at: str
    activated_at: str | None = None
    previous_version: str | None = None
    reason: str = ""
    metrics: SkillMetrics = field(default_factory=SkillMetrics)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> SkillRecord:
        data = dict(raw)
        data["status"] = SkillStatus(data["status"])
        data["metrics"] = SkillMetrics(**data.get("metrics", {}))
        return cls(**data)


@dataclass(frozen=True)
class ValidationReport:
    allowed: bool
    digest: str
    reasons: tuple[str, ...]
    outputs: tuple[str, ...] = ()


class SkillGenerator(Protocol):
    def generate(self, proposal: SkillProposal, destination: Path) -> Path: ...


class IsolatedSkillRunner(Protocol):
    def run(
        self,
        package: Path,
        commands: tuple[tuple[str, ...], ...],
        timeout_seconds: int,
    ) -> tuple[bool, tuple[str, ...]]: ...


class DockerSkillRunner:
    def __init__(self, image: str = "python:3.12-slim") -> None:
        self.image = image

    def run(
        self,
        package: Path,
        commands: tuple[tuple[str, ...], ...],
        timeout_seconds: int,
    ) -> tuple[bool, tuple[str, ...]]:
        outputs: list[str] = []
        for command in commands:
            argv = [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--pids-limit",
                "128",
                "--memory",
                "512m",
                "--cpus",
                "1",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=64m",
                "-v",
                f"{package.resolve()}:/skill:ro",
                "-w",
                "/skill",
                self.image,
                *command,
            ]
            try:
                result = subprocess.run(
                    argv,
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                return False, (*outputs, str(exc))
            output = (result.stdout + result.stderr).strip()[-4000:]
            outputs.append(output)
            if result.returncode:
                return False, tuple(outputs)
        return True, tuple(outputs)


class SkillValidator:
    def __init__(
        self,
        runner: IsolatedSkillRunner,
        *,
        max_files: int = 100,
        max_bytes: int = 1_000_000,
        timeout_seconds: int = 300,
    ) -> None:
        self.runner = runner
        self.max_files = max_files
        self.max_bytes = max_bytes
        self.timeout_seconds = timeout_seconds

    def validate(self, package: Path) -> ValidationReport:
        package = package.resolve()
        manifest = SkillManifest.load(package)
        reasons: list[str] = []
        if not (package / "SKILL.md").is_file():
            reasons.append("SKILL.md ausente")
        entrypoint = (package / manifest.entrypoint).resolve()
        if package not in entrypoint.parents or not entrypoint.is_file():
            reasons.append("entrypoint ausente ou fora do pacote")
        files = sorted(path for path in package.rglob("*") if path.is_file())
        if len(files) > self.max_files:
            reasons.append("quantidade máxima de arquivos excedida")
        size = sum(path.stat().st_size for path in files)
        if size > self.max_bytes:
            reasons.append("tamanho máximo do pacote excedido")
        for path in files:
            if path.is_symlink():
                reasons.append(f"link simbólico proibido: {path.name}")
                continue
            try:
                relative = path.resolve().relative_to(package)
            except ValueError:
                reasons.append(f"arquivo escapou do pacote: {path.name}")
                continue
            if "__pycache__" in relative.parts or relative.name.startswith("."):
                reasons.append(f"arquivo não permitido: {relative}")
            if path.suffix.lower() in {".py", ".sh", ".ps1", ".js", ".ts"}:
                content = path.read_text("utf-8", errors="replace")
                for pattern in _DANGEROUS:
                    if pattern.search(content):
                        reasons.append(f"padrão perigoso em {relative}: {pattern.pattern}")
        digest = self.digest(package)
        if reasons:
            return ValidationReport(False, digest, tuple(sorted(set(reasons))))
        passed, outputs = self.runner.run(
            package, manifest.validation_commands, self.timeout_seconds
        )
        if not passed:
            reasons.append("validação funcional isolada falhou")
        return ValidationReport(not reasons, digest, tuple(reasons), outputs)

    @staticmethod
    def digest(package: Path) -> str:
        checksum = hashlib.sha256()
        for path in sorted(item for item in package.rglob("*") if item.is_file()):
            relative = path.relative_to(package).as_posix()
            checksum.update(relative.encode())
            checksum.update(b"\0")
            checksum.update(path.read_bytes())
            checksum.update(b"\0")
        return checksum.hexdigest()


class RegistryConflict(RuntimeError):
    pass


class SkillRegistry:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.path = root / "registry.json"
        self._loaded_digest: str | None = None

    def load(self) -> dict[str, list[SkillRecord]]:
        if not self.path.exists():
            self._loaded_digest = None
            return {}
        content = self.path.read_bytes()
        self._loaded_digest = hashlib.sha256(content).hexdigest()
        raw = json.loads(content)
        return {
            skill_id: [SkillRecord.from_dict(item) for item in records]
            for skill_id, records in raw.items()
        }

    def save(self, records: dict[str, list[SkillRecord]]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        current = (
            hashlib.sha256(self.path.read_bytes()).hexdigest()
            if self.path.exists()
            else None
        )
        if current != self._loaded_digest:
            raise RegistryConflict("registro de skills alterado concorrentemente")
        payload = {
            skill_id: [asdict(item) for item in versions]
            for skill_id, versions in sorted(records.items())
        }
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)
        self._loaded_digest = hashlib.sha256(self.path.read_bytes()).hexdigest()

    def get(self, skill_id: str, version: str | None = None) -> SkillRecord:
        versions = self.load().get(skill_id, [])
        matches = [
            item for item in versions
            if version is None or item.version == version
        ]
        if not matches:
            raise KeyError(f"skill não encontrada: {skill_id}@{version or 'ativa'}")
        if version is None:
            active = [item for item in matches if item.status is SkillStatus.ACTIVE]
            if active:
                return active[-1]
        return matches[-1]


class SkillGapDetector:
    def __init__(self, minimum_occurrences: int = 2) -> None:
        self.minimum_occurrences = minimum_occurrences

    def detect(self, events: list[dict[str, Any]]) -> list[SkillProposal]:
        grouped: dict[str, list[str]] = {}
        for event in events:
            data = event.get("data", {})
            capability = data.get("missing_capability")
            kind = event.get("event", "")
            if not capability or kind not in {
                "skill.missing",
                "intent.unsupported",
                "tool.unavailable",
            }:
                continue
            grouped.setdefault(str(capability), []).append(str(event.get("id", "")))
        return [
            SkillProposal.from_gap(capability, evidence)
            for capability, evidence in sorted(grouped.items())
            if len(set(evidence)) >= self.minimum_occurrences
        ]


class SkillLifecycle:
    def __init__(
        self,
        root: Path,
        validator: SkillValidator,
        *,
        protected_permissions: frozenset[str] = _PROTECTED,
        audit: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.root = root
        self.packages = root / "packages"
        self.staging = root / "staging"
        self.registry = SkillRegistry(root)
        self.validator = validator
        self.protected_permissions = protected_permissions
        self.audit = audit or (lambda _event, _data: None)

    def create(
        self,
        proposal: SkillProposal,
        generator: SkillGenerator,
        *,
        approvals: set[str] | None = None,
        activate: bool = True,
    ) -> SkillRecord:
        destination = self.staging / f"{proposal.id}-{uuid4().hex[:8]}"
        destination.mkdir(parents=True, exist_ok=False)
        self.audit("skill.generation_started", {"skill": proposal.id})
        try:
            package = generator.generate(proposal, destination).resolve()
            if destination.resolve() not in {package, *package.parents}:
                raise ValueError("gerador retornou pacote fora da área de staging")
            generated = SkillManifest.load(package)
            if generated.id != proposal.id:
                raise ValueError("a skill gerada não corresponde à proposta")
            record = self.install(package, approvals=approvals, activate=activate)
            self.audit(
                "skill.generation_completed",
                {"skill": record.id, "version": record.version},
            )
            return record
        except Exception as exc:
            self.audit(
                "skill.generation_failed",
                {"skill": proposal.id, "reason": str(exc)},
            )
            raise
        finally:
            shutil.rmtree(destination, ignore_errors=True)

    def install(
        self,
        package: Path,
        *,
        approvals: set[str] | None = None,
        activate: bool = True,
    ) -> SkillRecord:
        manifest = SkillManifest.load(package)
        report = self.validator.validate(package)
        if not report.allowed:
            raise ValueError("; ".join(report.reasons))
        records = self.registry.load()
        versions = records.setdefault(manifest.id, [])
        if any(item.version == manifest.version for item in versions):
            raise ValueError("versão da skill já registrada")
        if versions:
            newest = max(tuple(int(part) for part in item.version.split(".")) for item in versions)
            candidate = tuple(int(part) for part in manifest.version.split("."))
            if candidate <= newest:
                raise ValueError("a nova versão deve ser superior às versões registradas")
        self._validate_dependencies(manifest, records)
        target = self.packages / manifest.id / manifest.version / report.digest
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(package, target)
        record = SkillRecord(
            id=manifest.id,
            version=manifest.version,
            digest=report.digest,
            package_path=str(target),
            status=SkillStatus.VALIDATED,
            permissions=list(manifest.permissions),
            dependencies=list(manifest.dependencies),
            created_at=datetime.now(UTC).isoformat(),
        )
        versions.append(record)
        self.registry.save(records)
        self.audit(
            "skill.validated",
            {"skill": record.id, "version": record.version, "digest": record.digest},
        )
        if activate:
            return self.activate(manifest.id, manifest.version, approvals=approvals)
        return record

    def activate(
        self,
        skill_id: str,
        version: str,
        *,
        approvals: set[str] | None = None,
    ) -> SkillRecord:
        approvals = approvals or set()
        records = self.registry.load()
        versions = records.get(skill_id, [])
        selected = next((item for item in versions if item.version == version), None)
        if selected is None:
            raise KeyError(f"skill não encontrada: {skill_id}@{version}")
        if selected.status not in {SkillStatus.VALIDATED, SkillStatus.ROLLED_BACK}:
            raise ValueError(f"status não ativável: {selected.status.value}")
        protected = self.protected_permissions.intersection(selected.permissions)
        missing = protected.difference(approvals)
        if missing:
            raise PermissionError(
                "aprovações ausentes para permissões protegidas: "
                + ", ".join(sorted(missing))
            )
        self._verify_integrity(selected)
        active = next(
            (item for item in versions if item.status is SkillStatus.ACTIVE), None
        )
        if active:
            active.status = SkillStatus.SUPERSEDED
            selected.previous_version = active.version
        selected.status = SkillStatus.ACTIVE
        selected.activated_at = datetime.now(UTC).isoformat()
        selected.reason = ""
        self.registry.save(records)
        self.audit(
            "skill.activated",
            {"skill": skill_id, "version": version, "previous": selected.previous_version},
        )
        return selected

    def record(
        self,
        skill_id: str,
        *,
        success: bool,
        latency_seconds: float,
        cost: float = 0.0,
        safety_failure: bool = False,
    ) -> SkillRecord:
        if latency_seconds < 0 or cost < 0:
            raise ValueError("latência e custo não podem ser negativos")
        records = self.registry.load()
        record = self._active(records, skill_id)
        record.metrics.invocations += 1
        record.metrics.successes += int(success)
        record.metrics.failures += int(not success)
        record.metrics.safety_failures += int(safety_failure)
        record.metrics.total_latency_seconds += latency_seconds
        record.metrics.total_cost += cost
        self.registry.save(records)
        return record

    def reconcile(self) -> list[SkillRecord]:
        records = self.registry.load()
        changed: list[SkillRecord] = []
        for skill_id in sorted(records):
            try:
                record = self._active(records, skill_id)
                manifest = SkillManifest.load(Path(record.package_path))
                self._verify_integrity(record)
                active_ids = {
                    candidate_id
                    for candidate_id, versions in records.items()
                    if any(item.status is SkillStatus.ACTIVE for item in versions)
                }
                missing_dependencies = set(record.dependencies).difference(active_ids)
                if missing_dependencies:
                    raise ValueError(
                        "dependências deixaram de estar ativas: "
                        + ", ".join(sorted(missing_dependencies))
                    )
                metrics = record.metrics
                unhealthy = (
                    metrics.safety_failures > 0
                    or (
                        metrics.invocations >= manifest.minimum_observations
                        and (
                            metrics.success_rate < manifest.minimum_success_rate
                            or metrics.average_latency_seconds
                            > manifest.max_latency_seconds
                        )
                    )
                )
                if unhealthy:
                    changed.append(self.rollback(skill_id, "SLO ou segurança degradados"))
            except (KeyError, ValueError) as exc:
                active = next(
                    (
                        item
                        for item in records.get(skill_id, [])
                        if item.status is SkillStatus.ACTIVE
                    ),
                    None,
                )
                if active:
                    active.status = SkillStatus.QUARANTINED
                    active.reason = str(exc)
                    changed.append(active)
                    self.audit(
                        "skill.quarantined",
                        {"skill": skill_id, "reason": str(exc)},
                    )
        if changed:
            fresh = self.registry.load()
            for item in changed:
                for current in fresh.get(item.id, []):
                    if current.version == item.version:
                        current.status = item.status
                        current.reason = item.reason
            self.registry.save(fresh)
        return changed

    def rollback(self, skill_id: str, reason: str) -> SkillRecord:
        records = self.registry.load()
        current = self._active(records, skill_id)
        current.status = SkillStatus.ROLLED_BACK
        current.reason = reason
        restored = next(
            (
                item
                for item in records[skill_id]
                if item.version == current.previous_version
            ),
            None,
        )
        if restored:
            self._verify_integrity(restored)
            restored.status = SkillStatus.ACTIVE
            restored.activated_at = datetime.now(UTC).isoformat()
        self.registry.save(records)
        self.audit(
            "skill.rolled_back",
            {
                "skill": skill_id,
                "version": current.version,
                "restored": restored.version if restored else None,
                "reason": reason,
            },
        )
        return current

    def quarantine(self, skill_id: str, reason: str) -> SkillRecord:
        records = self.registry.load()
        record = self._active(records, skill_id)
        record.status = SkillStatus.QUARANTINED
        record.reason = reason
        self.registry.save(records)
        self.audit("skill.quarantined", {"skill": skill_id, "reason": reason})
        return record

    def run_once(
        self,
        events: list[dict[str, Any]],
        generator: SkillGenerator,
        *,
        approvals: set[str] | None = None,
        minimum_occurrences: int = 2,
        max_skills: int = 1,
    ) -> list[SkillRecord]:
        self.reconcile()
        proposals = SkillGapDetector(minimum_occurrences).detect(events)
        existing = self.registry.load()
        created: list[SkillRecord] = []
        for proposal in proposals:
            versions = existing.get(proposal.id, [])
            needs_revision = any(
                item.status in {SkillStatus.QUARANTINED, SkillStatus.ROLLED_BACK}
                for item in versions
            )
            if (versions and not needs_revision) or len(created) >= max_skills:
                continue
            created.append(
                self.create(
                    proposal,
                    generator,
                    approvals=approvals,
                    activate=True,
                )
            )
        return created

    def _verify_integrity(self, record: SkillRecord) -> None:
        package = Path(record.package_path)
        if not package.is_dir():
            raise ValueError("pacote registrado não existe")
        if self.validator.digest(package) != record.digest:
            raise ValueError("integridade do pacote violada")

    @staticmethod
    def _active(
        records: dict[str, list[SkillRecord]], skill_id: str
    ) -> SkillRecord:
        active = [
            item for item in records.get(skill_id, [])
            if item.status is SkillStatus.ACTIVE
        ]
        if len(active) != 1:
            raise KeyError(f"skill ativa não encontrada: {skill_id}")
        return active[0]

    @staticmethod
    def _validate_dependencies(
        manifest: SkillManifest,
        records: dict[str, list[SkillRecord]],
    ) -> None:
        if manifest.id in manifest.dependencies:
            raise ValueError("skill não pode depender de si mesma")
        active = {
            skill_id
            for skill_id, versions in records.items()
            if any(item.status is SkillStatus.ACTIVE for item in versions)
        }
        missing = set(manifest.dependencies).difference(active)
        if missing:
            raise ValueError("dependências ativas ausentes: " + ", ".join(sorted(missing)))


class CommandSkillGenerator:
    def __init__(self, command: tuple[str, ...], timeout_seconds: int = 900) -> None:
        if not command:
            raise ValueError("comando gerador obrigatório")
        self.command = command
        self.timeout_seconds = timeout_seconds

    def generate(self, proposal: SkillProposal, destination: Path) -> Path:
        request = destination / "proposal.json"
        request.write_text(
            json.dumps(asdict(proposal), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        environment = {
            "PATH": os.environ.get("PATH", ""),
            "REA_OLLAMA_URL": os.environ.get(
                "REA_OLLAMA_URL", "http://localhost:11434"
            ),
            "REA_SKILL_PROPOSAL": str(request),
            "REA_SKILL_OUTPUT": str(destination),
        }
        result = subprocess.run(
            [*self.command, str(request), str(destination)],
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            check=False,
            env=environment,
        )
        if result.returncode:
            raise RuntimeError((result.stdout + result.stderr).strip()[-4000:])
        return destination



class SkillRuntime:
    def __init__(
        self,
        lifecycle: SkillLifecycle,
        *,
        image: str = "python:3.12-slim",
        timeout_seconds: int = 120,
        executor: Callable[
            [Path, SkillManifest, dict[str, Any], int],
            tuple[bool, str],
        ]
        | None = None,
    ) -> None:
        self.lifecycle = lifecycle
        self.image = image
        self.timeout_seconds = timeout_seconds
        self.executor = executor or self._docker_execute

    def invoke(
        self,
        skill_id: str,
        payload: dict[str, Any],
        *,
        granted_permissions: set[str] | None = None,
    ) -> Any:
        record = self.lifecycle.registry.get(skill_id)
        self.lifecycle._verify_integrity(record)
        protected = self.lifecycle.protected_permissions.intersection(record.permissions)
        missing = protected.difference(granted_permissions or set())
        if missing:
            raise PermissionError(
                "permissões não concedidas para esta execução: "
                + ", ".join(sorted(missing))
            )
        package = Path(record.package_path)
        manifest = SkillManifest.load(package)
        started = time.monotonic()
        success = False
        safety_failure = False
        try:
            success, output = self.executor(
                package,
                manifest,
                payload,
                self.timeout_seconds,
            )
            if not success:
                raise RuntimeError(output or "execução da skill falhou")
            try:
                return json.loads(output)
            except json.JSONDecodeError:
                return output
        except PermissionError:
            safety_failure = True
            raise
        finally:
            latency = time.monotonic() - started
            self.lifecycle.record(
                skill_id,
                success=success,
                latency_seconds=latency,
                safety_failure=safety_failure,
            )
            self.lifecycle.reconcile()

    def _docker_execute(
        self,
        package: Path,
        manifest: SkillManifest,
        payload: dict[str, Any],
        timeout_seconds: int,
    ) -> tuple[bool, str]:
        argv = [
            "docker",
            "run",
            "--rm",
            "-i",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "128",
            "--memory",
            "512m",
            "--cpus",
            "1",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            "-e",
            "PYTHONDONTWRITEBYTECODE=1",
            "-v",
            f"{package.resolve()}:/skill:ro",
            "-w",
            "/skill",
            self.image,
            "python",
            manifest.entrypoint,
        ]
        try:
            result = subprocess.run(
                argv,
                input=json.dumps(payload, ensure_ascii=False),
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, str(exc)
        output = (result.stdout + result.stderr).strip()[-10000:]
        return result.returncode == 0, output


def temporary_skill_root() -> Path:
    return Path(tempfile.mkdtemp(prefix="rea-skills-"))
