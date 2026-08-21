# Ramathix Engineering AI

**Ramathix Engineering AI (REA)** is a local-first, governed multi-agent software engineering platform. Its goal is to evolve from a local copilot into an autonomous engineering organization composed of Engineering Manager, Tech Lead, Senior Developers, QA, Security, Performance, SRE, Cloud Architecture and FinOps agents.

The project intentionally separates **LLM reasoning** from **execution authority**. Models may request actions, but command execution, production access, cost decisions and future merges are controlled by deterministic governance components.

## V0.1

The first milestone provides the safety and runtime foundation:

- CLI-first interface
- GitHub Issues as work units
- Ollama-backed Model Router
- Tech Lead structured issue analysis
- Docker sandbox execution
- central command allowlist
- cost approval gate
- append-only audit log with secret redaction
- GitHub PR adapter (draft by default)

See [`docs/architecture/V0.1.md`](docs/architecture/V0.1.md) for the architecture and invariants.

## Requirements

- Python 3.11+
- Docker
- Ollama
- Git
- GitHub token for private repository issue access

## Install

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
```

On Windows PowerShell, activate with `.venv\\Scripts\\Activate.ps1`.

## First commands

```bash
rea status
rea models status
rea policy check "git status"
rea policy check "terraform apply"
rea issue analyze 1 --repo tbarletta/ramathix-engineering-ai
rea sandbox run "pytest" --workspace . --image python:3.12-slim
```

The sandbox has networking disabled by default. A command must be explicitly allowed by `config/policies/commands.yaml` before it can run.

## Model routing

`config/models.yaml` maps roles to model capabilities instead of permanently binding a role to a single LLM. This allows models and runtimes to be replaced without changing agent or governance logic.

## Governance

Priority order for knowledge and decisions:

1. explicit human-approved decision
2. approved ADR
3. documented business rule
4. production code
5. documentation
6. Git history
7. AI inference

Any decision that may create or increase monetary cost requires human approval, including in future autonomous operating modes.

## Roadmap

- **V0.1 Foundation:** CLI, model routing, sandbox, command policy, audit, GitHub Issue adapter
- **V0.2 Knowledge:** repository reverse engineering, AST/RAG, Git history, architecture graph
- **V0.3 First Team:** Engineering Manager, Tech Lead, Senior Developer, Reviewer
- **V0.4 Level 6:** Issue -> plan -> branch -> implementation -> tests -> review -> draft PR
- **V0.5 Specialization:** Backend, Frontend, Mobile, Database, DevOps, QA
- **V0.6 Production:** SRE, incidents, read-only production diagnostics, postmortems
- **V0.7 Governance:** Security, Performance, Cloud Architect, FinOps, formal risk engine
- **V1.0:** governed multi-agent AI engineering organization
