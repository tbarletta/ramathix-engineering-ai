# Ramathix Engineering AI

**Ramathix Engineering AI (REA)** is a local-first, governed multi-agent software engineering
platform. You talk to it in a terminal like you would to any AI coding assistant, but every
action that could change a repository, publish something externally, or cost money passes
through a deterministic approval gate before it happens — never the LLM's own judgment alone.

The project deliberately separates **LLM reasoning** from **execution authority**. Models draft
plans, RFCs, code changes and risk assessments; a set of plain Python components — a command
policy engine, a risk engine, a portfolio planner, an append-only audit log — decide what is
actually allowed to run, and everything that runs is logged.

## Quickstart

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
python -m pip install -e '.[dev]'
cp .env.example .env
```

Requirements: Python 3.11+, [Ollama](https://ollama.com) running locally, Docker (for sandboxed
test execution), Git, and a `GITHUB_TOKEN` for private repositories.

Start a conversational session — this is the main entry point for everything below:

```bash
rea
```

The session answers in Brazilian Portuguese, keeps context for the terminal session, and — when
started inside a Git repository — refreshes the deterministic Knowledge Engine inventory first and
gives the assistant a bounded, factual summary of the repository (never invented details). Type
`/exit` to close it, `/ajuda` at any time for the full list of conversational commands.

## How a session works

Every message you type goes through the same pipeline:

1. A handful of deterministic, instant patterns are checked first (`/aprovar`, `/cancelar`,
   `/status`, `/modo …`, an explicit GitHub URL, "crie um roadmap…").
2. If nothing matches, a lightweight local-model **intent classifier** looks at the message
   (and whether a repository is already cloned) and decides whether it's a clone request, a git
   operation, a file-read request, a roadmap proposal, a phase execution — or just conversation.
3. Anything that would **change local repository state, publish something externally, or cost
   money** is either run immediately (if it's a pure read) or shown to you as an exact preview —
   the literal `git` command, the RFC, the list of Issues about to be created — and waits for
   `/aprovar`. Nothing is ever described as done before the governed controller actually returns
   that result.
4. Anything else falls through to free-form chat, streamed token-by-token as it's generated. A
   live status line (`⠋ REA: Redigindo a RFC... (12s)`) shows what's happening for as long as a
   step takes, instead of leaving you staring at a blank prompt.

### What you can just ask for, in plain language

- **Clone a repository** — paste a GitHub URL (`clone https://github.com/owner/repo`, or mention
  it once and say "baixa esse repositório" later). It clones into the current directory if empty,
  or into a subdirectory named after the repo otherwise, and immediately maps its knowledge.
- **Any git operation** — "troca pra branch X", "dá um pull", "qual o status?", "lista as tags",
  "faz merge da branch Y". The classifier translates it into the real `git` command. Pure reads
  (`status`, `log`, `diff`, `branch -a`, `tag`, `remote -v`) run immediately; anything that
  changes state shows the exact command first. Commands matching a known-destructive pattern
  (`reset --hard`, `push --force`, `branch -D`, `rebase`, `clean`) get an extra warning but are
  never silently blocked — you decide.
- **Read a file or list a directory** — "mostra o package.json", "lista os arquivos de src/" —
  pure reads, run instantly, no approval needed, content is redacted for secret-shaped strings
  before being shown and confined to the repository root.
- **Propose a project or ask for an improvement roadmap** — this always drafts an **RFC** first
  (context, scope in/out, technical approach, alternatives considered, risks, acceptance
  criteria, a phase estimate) and shows it in full. Only after you `/aprovar` the RFC does REA
  generate the executable roadmap — Initiatives → Projects → Work Units, prioritized and
  dependency-ordered. `implemente a fase 1` then prepares that phase's GitHub Issues for another
  explicit approval, and `/executar WU-001` prepares a governed Level 6 run (worktree, sandboxed
  tests, independent review, commit, push, draft PR) behind a third.
- **Natural approval/cancellation** — while something is pending, "aprovado, pode seguir" or
  "cancela isso" work the same as `/aprovar`/`/cancelar`. This exists specifically so a natural
  reply never falls through to free chat and gets narrated as if it had happened — that's the one
  failure mode the whole governance model exists to prevent.

### Session modes

```text
/modo planejamento   REA continues analyzing, drafting RFCs and generating roadmaps (those only
                      ever write REA's own .rea/ bookkeeping), but /aprovar refuses to execute
                      anything that would touch the repository or GitHub — cloning, git commands,
                      publishing Issues, Level 6 execution — until you leave this mode.

/modo automatico      Skips /aprovar for local, reversible git state changes only: cloning,
                      checkout, pull, fetch, merge, stash. Publishing Issues, Level 6
                      (commit/push/PR), and anything flagged high-risk still require explicit
                      approval, regardless of mode.

/modo padrao          Back to the default: reads run free, everything else asks first.
```

The prompt shows the active mode (`[automático] você>`). `/status` also reports it.

## Governance model

The pending-action pattern above is implemented once, generically, in the conversation
controller — every governed action (clone, git command, RFC→roadmap, Issue publication, Level 6
execution) is a `Pending*` value; `/aprovar` resolves whichever one is outstanding, and a failed
approval preserves it so you can retry instead of losing the request.

Underneath that, three independent layers actually decide what's allowed:

- **Command policy** (`config/policies/commands.yaml`) — an allow/ask/deny/cost-approval
  allowlist for every local command REA might run. Pure reads and safe local writes (`git
  status`, `git commit`, running the project's own test suite) are `allow`; anything that mutates
  shared state (`git push`, `git clone`, `checkout`, `pull`, `merge`, `stash`, installing
  dependencies) is `ask`; provisioning cloud resources is `cost_approval` (always requires an
  explicit human token, never inferred from a general approval); `sudo`, `rm -rf /`,
  `kubectl delete`, `docker system prune` are hard `deny`.
- **Risk engine** (`governance/risk.py`) — for organization Work Units and Level 6 execution:
  Security, Performance, Cloud Architect and FinOps specialist agents produce findings, but a
  deterministic engine owns the final decision. Precedence is fixed: a declared production write
  is always `blocked`; any cost impact is always `cost_approval`; critical risk needs explicit
  human approval; high risk needs explicit Tech Lead approval; medium is governed; low is
  autonomous. A specialist can escalate risk, never lower what was already detected.
- **Audit log** (`.rea/audit.jsonl`) — every policy check, command execution, governance
  decision and organization/RFC/Level 6 milestone is appended as a JSON record, with secrets
  redacted (`governance/redaction.py`) before being written or ever shown — including mid-stream:
  a `StreamRedactor` holds back an unterminated PEM-style key block until its closing marker
  arrives (or the response ends), so a secret can't flash on screen before it's caught.

Priority order when REA has to decide what's true about a repository or a decision: an explicit
human-approved decision, then an approved ADR, then a documented business rule, then production
code, then documentation, then Git history, then AI inference last. Any action with a monetary
cost implication requires human approval — there is no autonomous mode that skips this.

## Architecture

```text
cli.py                     conversational REPL + all `rea …` subcommands (entry point)
conversation.py            free-chat assistant: streaming replies, redaction, system prompt
conversation_actions.py    the governed-action controller: intent routing, Pending* states,
                            session modes, natural-language approval, RFC/roadmap orchestration

knowledge/                 deterministic repository reverse engineering (no LLM): languages,
                            manifests, frameworks, persistence/queues/infra, AST symbols and
                            import graph, governed Git history, confidence-tagged facts

organization/               AI Engineering Manager: strategic goal → RFC → Initiatives →
                            Projects → Work Units, deterministic priority + dependency ordering,
                            risk preflight, GitHub Issue publication
team/                       first AI team: Engineering Manager → Tech Lead → Senior Developer →
                            Code Reviewer, structured work packages and independent review
specialists/                routes Tech Lead tasks to Backend/Frontend/Mobile/Database/
                            DevOps-SRE/QA agents while reusing the same Level 6 engine
governance/                 Security/Performance/Cloud/FinOps specialist agents + the
                            deterministic risk engine that owns the final decision
level6/                     the autonomous Issue-to-PR engine: isolated git worktree, bounded
                            implement→validate→review loop, commit, approved push, draft PR
production/                 read-only production incident diagnosis (logs/metrics/traces/
                            deploys/Git correlation) — proposals only, no production write path

execution.py, policy.py     governed local command execution against the command policy
sandbox.py                  Docker sandbox for test/validation commands (network off by default)
audit.py, governance/
  redaction.py               append-only audit log; secret redaction, including a streaming-safe
                             buffered variant
github.py                   GitHub REST client + URL/slug parsing
models.py                   Ollama client (blocking, streaming, and structured-JSON chat) +
                             role→model router
config.py                   settings resolution: env var overrides, checkout config vs the
                             bundled fallback copy under resources/
```

## Memory / persistence (`.rea/`)

Everything REA remembers across commands lives under `.rea/` in the current `REA_HOME` (the
working directory by default) — nothing is silently kept only in a chat session's memory:

| Path | What's in it |
|---|---|
| `.rea/knowledge/` | One JSON inventory per repository scanned: languages, manifests, facts, symbols, architecture, Git summary. Refreshed on every `rea` startup and after every clone. |
| `.rea/rfcs/` | Every drafted RFC (`rfc-YYYYMMDD-xxxxxxxx.json`) — context, scope, approach, alternatives, risks, acceptance criteria, phase estimate. Written before a roadmap exists. |
| `.rea/organization/` | Every generated roadmap (`org-YYYYMMDD-xxxxxxxx.json`) — Initiatives, Projects, Work Units, priority, dependencies, governance state, published Issue references. Publication state is saved after each Issue creation, so a partial failure never discards already-published work. `/usar <plan-id>` resumes one in a later session. |
| `.rea/work/` | Structured work packages from the first AI team and Level 6 execution artifacts (per-iteration coding/validation/review records). |
| `.rea/worktrees/` | Isolated Git worktrees Level 6 creates per Issue — the base branch is never edited directly. |
| `.rea/incidents/` | Production incident analyses and Markdown postmortems from the read-only SRE workflow. |
| `.rea/audit.jsonl` | The append-only, secret-redacted record of every policy check, command run, and governance/organization/RFC/Level 6 event. |

The in-terminal chat history itself (the last 12 turns, used so the assistant remembers what you
just said) is **not** persisted — it lives only for the current process. What *is* durable across
restarts is exactly the artifacts above, plus whatever the governed actions actually changed in
the repository itself (commits, branches, GitHub Issues/PRs).

## Configuration

- **`config/models.yaml`** — maps three roles (`reasoner`, `coder`, `utility`) to an Ollama model
  each, and routes every specialist (`tech_lead`, `senior_backend`, `security`, `finops`, …) to
  one of those roles. Swapping a model means editing this file — no agent or governance code
  changes. All three roles currently point at the same model deliberately: on a single GPU, two
  different models loaded at once can exceed available VRAM and force Ollama to reload from disk
  every time REA switches between an intent-classification call and a drafting call.
- **`config/policies/commands.yaml`** — the command allowlist described above.
- **Env vars** (see `.env.example`) — `REA_HOME`, `REA_OLLAMA_URL`, `REA_AUDIT_PATH`,
  `REA_COMMAND_POLICY`, `REA_MODEL_CONFIG`, `REA_KNOWLEDGE_PATH`, `REA_WORK_PATH`,
  `REA_WORKTREE_PATH` override the defaults above. Config files are resolved from the current
  checkout first, falling back to a bundled copy under `src/rea/resources/` for installs that run
  outside a checkout — the two are kept identical (enforced by a test).

## CLI reference

```bash
rea                                    # start the conversational session (default, no args)
rea init . [--workspace]               # map one repo, or every Git repo under a workspace
rea status                             # current settings and storage paths
rea models status                      # which Ollama model backs each role, and whether it's pulled
rea policy check "git status"          # what the command policy decides for a given command

rea repo scan ../some-repo [--json]    # deterministic knowledge scan
rea repo list                          # every repository REA has mapped

rea org plan "<goal>" --repo o/r [--constraint "..."]
rea org list / rea org show <plan-id>
rea org publish <plan-id> --approve-rule github-issue-create [--unit WU-...] [--approve-tech-lead WU-...] [--approve-human WU-...] [--approve-cost WU-...]

rea governance assess .rea/work/issue-N-plan.json

rea team plan <issue> --repo o/r       # first-team structured work package
rea team review <plan.json>
rea-agents capabilities                # specialist routing table
rea-agents route-plan <plan.json>

rea issue analyze <issue> --repo o/r
rea issue run <issue> --repo o/r --workspace ../repo --base main --approve-rule git-push [--approve-rule dependency-install --allow-network]

rea production policy
rea production inspect --service X --signals ./snap.jsonl [--git-repo ../repo]
rea incident analyze INC-ID --service X --title "..." --signals ./snap.jsonl [--git-repo ../repo]

rea sandbox run "pytest" --workspace . --image python:3.12-slim
```

Sandboxed commands have networking disabled by default and must be explicitly allowed by the
command policy before they run.

## Development

```bash
python -m pytest        # 138 tests
python -m ruff check .  # lint
```

## Milestone history

REA was built incrementally; each milestone's detailed design doc is under `docs/architecture/`.

| Milestone | What it added |
|---|---|
| [V0.1](docs/architecture/V0.1.md) | CLI, model routing, Docker sandbox, command allowlist, audit log, draft-only GitHub PR adapter |
| [V0.2](docs/architecture/V0.2.md) | Deterministic Knowledge Engine — languages, AST/import graph, governed Git history, multi-repo catalog |
| [V0.3](docs/architecture/V0.3.md) | First AI team: Engineering Manager → Tech Lead → Senior Developer → Reviewer |
| [V0.4](docs/architecture/V0.4.md) | Level 6: governed Issue → worktree → implementation → sandboxed tests → review → draft PR |
| [V0.5](docs/architecture/V0.5.md) | Specialist routing: Backend, Frontend, Mobile, Database, DevOps/SRE, QA |
| [V0.6](docs/architecture/V0.6.md) | Read-only production/SRE diagnostics and postmortems |
| [V0.7](docs/architecture/V0.7.md) | Deterministic risk engine + Security/Performance/Cloud/FinOps specialist gate |
| [V1.0](docs/architecture/V1.0.md) | AI Engineering Manager: strategic goal → prioritized, dependency-ordered Work Unit portfolio |
| *(unreleased)* | Conversational governance: clone, natural-language git, RFC-gated roadmap proposal, read-only explore, streaming replies, live progress, Plan/Auto session modes |
