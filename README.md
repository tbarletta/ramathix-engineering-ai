# Ramathix Engineering AI

**Ramathix Engineering AI (REA)** is a local-first, governed multi-agent software engineering
platform. Its goal is to evolve from a local copilot into an autonomous engineering organization
composed of Engineering Manager, Tech Lead, Senior Developers, QA, Security, Performance, SRE,
Cloud Architecture and FinOps agents.

The project intentionally separates **LLM reasoning** from **execution authority**. Models may
request actions, but command execution, production access, cost decisions and future merges are
controlled by deterministic governance components.

## V0.1 Foundation

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

See [`docs/architecture/V0.1.md`](docs/architecture/V0.1.md).

## V0.2 Knowledge Engine

V0.2 adds deterministic repository reverse engineering before LLM inference:

- languages, manifests and frameworks
- persistence, queues and messaging
- infrastructure, CI/CD, tests and API endpoints
- Python symbols and TypeScript/JavaScript import edges
- governed Git history
- dependency graph and multi-repository catalog
- evidence and confidence for every knowledge fact

```bash
rea repo scan ../social-media
rea repo scan ../ramathix-ai-core --json
rea repo list
```

Inventories are stored under `.rea/knowledge/` by default. Deterministic findings are
`confirmed`; architecture or business inference must use `inferred_*` confidence levels.

See [`docs/architecture/V0.2.md`](docs/architecture/V0.2.md).

## V0.3 First AI Team

V0.3 introduces the first functional engineering team:

```text
Engineering Manager
        |
        v
    Tech Lead
        |
        v
Senior Developer
        |
        v
  Code Reviewer
```

The team consumes GitHub Issues and V0.2 knowledge. It creates structured work packages and
independent reviews, but does not edit code or execute developer-proposed commands.

```bash
rea team plan 123 --repo tbarletta/social-media
rea team review .rea/work/issue-123-plan.json
```

See [`docs/architecture/V0.3.md`](docs/architecture/V0.3.md).

## V0.4 Level 6

V0.4 is the first governed autonomous Issue-to-PR workflow:

```text
Issue
  -> knowledge refresh
  -> Engineering Manager
  -> Tech Lead
  -> isolated worktree
  -> Senior Developer edits
  -> sandboxed tests
  -> independent source review
  -> correction loop
  -> commit
  -> approved push
  -> draft PR
```

Example:

```bash
rea issue run 123 \
  --repo tbarletta/social-media \
  --workspace ../social-media \
  --base master \
  --approve-rule git-push
```

Important V0.4 controls:

- the base branch is never edited directly
- only file paths approved by the technical plan may be changed
- `.env`, private keys and credential paths are blocked
- credential-like source content is redacted before model prompts
- generated hard-coded secrets are rejected before staging
- Docker networking is disabled by default and images are never auto-pulled
- `ASK` requires explicit `--approve-rule`
- `COST_APPROVAL` always requires a human and cannot be auto-approved
- Reviewer `request_changes` returns to the Developer
- the correction loop is bounded
- pull requests are always draft
- merge remains a human-controlled operation

For dependency installation, both the rule and network must be explicitly enabled:

```bash
rea issue run 123 \
  --repo tbarletta/social-media \
  --workspace ../social-media \
  --approve-rule git-push \
  --approve-rule dependency-install \
  --allow-network
```

See [`docs/architecture/V0.4.md`](docs/architecture/V0.4.md).

## Requirements

- Python 3.11+
- Docker
- Ollama
- Git
- GitHub token for private repository issue access
- pre-pulled Docker images for the target project

## Install

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
```

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1`.

## First commands

```bash
rea status
rea models status
rea policy check "git status"
rea policy check "terraform apply"
rea repo scan ../social-media
rea repo list
rea team plan 123 --repo tbarletta/social-media
rea team review .rea/work/issue-123-plan.json
rea sandbox run "pytest" --workspace . --image python:3.12-slim
```

The sandbox has networking disabled by default. A command must be explicitly allowed by
`config/policies/commands.yaml` before execution. Knowledge-engine subprocesses also use the same
central policy.

## Model routing

`config/models.yaml` maps roles to capabilities rather than permanently binding a role to a
single LLM. Models and runtimes can therefore be replaced without changing agent or governance
logic.

## Governance

Priority order for knowledge and decisions:

1. explicit human-approved decision
2. approved ADR
3. documented business rule
4. production code
5. documentation
6. Git history
7. AI inference

Any decision that may create or increase monetary cost requires human approval, including in
future autonomous operating modes.

## Roadmap

- **V0.1 Foundation:** CLI, model routing, sandbox, command policy, audit, GitHub Issue adapter
- **V0.2 Knowledge:** deterministic reverse engineering, AST/import graph, Git history, catalog
- **V0.3 First Team:** Engineering Manager, Tech Lead, Senior Developer, Reviewer
- **V0.4 Level 6:** Issue -> plan -> branch -> implementation -> tests -> review -> draft PR
- **V0.5 Specialization:** Backend, Frontend, Mobile, Database, DevOps, QA
- **V0.6 Production:** SRE, incidents, read-only production diagnostics, postmortems
- **V0.7 Governance:** Security, Performance, Cloud Architect, FinOps, formal risk engine
- **V1.0:** governed multi-agent AI engineering organization
