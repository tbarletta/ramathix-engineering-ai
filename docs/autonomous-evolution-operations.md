# Autonomous evolution operations

The V1.1 control plane is fully wired to Level 6, GitHub, isolated Git worktree benchmarks,
persistent worker state and guarded promotion. Environment-specific production commands remain
configuration, not application code.

## Prerequisites

1. Keep the self-hosted GitHub Actions runner online.
2. Protect `main`, require CI and prohibit direct pushes.
3. Set `GITHUB_TOKEN` with Issues and Pull Requests access. Do not grant repository administration.
4. Start Ollama and Docker as required by Level 6.
5. Review `config/evolution-benchmark.json` for the target repository.

## One experiment

Create a hypothesis JSON from `rea-evolve discover --output hypothesis.json`, select one item,
then run:

```bash
rea-evolve run hypothesis.json \
  --repo owner/repository \
  --workspace /workspace/repository \
  --approve-rule github-issue-create \
  --approve-rule git-push
```

The controller creates an Issue containing evidence and prior lessons, invokes Level 6, benchmarks
both refs in disposable worktrees, rejects regressions, persists the experiment and returns the
existing draft PR.

## Continuous worker

```bash
rea-evolve daemon \
  --repo owner/repository \
  --workspace /workspace/repository \
  --approve-rule github-issue-create \
  --approve-rule git-push \
  --max-experiments 2 \
  --max-gpu-minutes 60
```

Use `--once` from cron, systemd timers or GitHub Actions. The permanent daemon polls hourly by
default. It consumes local audit events plus failed GitHub workflow runs, uses a repository lock,
persists daily budgets and does not repeat a processed hypothesis.

## Promotion

```bash
rea-evolve promote EXPERIMENT_ID \
  --pr 123 \
  --repo owner/repository \
  --approve-rule auto-merge
```

Promotion fails closed unless `main` is protected, the candidate improved, critical capabilities
did not regress, changed paths are not protected, the PR is mergeable and every reported check is
green. An eligible draft is marked ready immediately before squash merge.

## Production

Copy `config/evolution-deployment.example.json` outside the repository, replace each argv array
with the environment's deployment CLI, and provide it to `CommandDeploymentAdapter`. Commands
never use a shell. Shadow health is checked first; production canary requires explicit approval;
an unhealthy canary is rolled back and rollback health is verified.

Production database migrations, governance changes, CI changes and promotion-policy changes never
qualify for autonomous promotion.
