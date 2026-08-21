# Free CI strategy

Ramathix Engineering AI uses the same validation gate locally and in pull requests.

## Local validation

Linux/macOS:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python scripts/validate.py
```

Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe scripts\validate.py
```

The gate executes:

1. Python bytecode compilation for `src` and `tests`.
2. `pip check` dependency consistency.
3. Ruff static analysis.
4. Pytest.

## GitHub pull-request validation

The repository workflow uses a GitHub Actions self-hosted Linux x64 runner:

```yaml
runs-on: [self-hosted, linux, x64]
```

Self-hosted runners do not consume GitHub-hosted runner minutes. No Actions artifact or cache upload is used by this workflow.

### Registering a repository runner

1. Open the repository on GitHub.
2. Go to **Settings → Actions → Runners**.
3. Choose **New self-hosted runner**.
4. Select **Linux** and **x64**.
5. Run the download/configuration commands shown by GitHub on a dedicated machine/user.
6. Start the runner with the command shown by GitHub, or install it as a service.
7. Confirm that the runner is shown as **Idle** before expecting PR jobs to execute.

## Security rules

- Use the self-hosted runner only for trusted private repositories.
- Run it as a dedicated, unprivileged OS user.
- Do not place production credentials, personal tokens, SSH private keys, or cloud admin credentials in the runner environment.
- Keep the runner workspace dedicated to CI.
- The workflow only receives `contents: read` through `GITHUB_TOKEN`.
- Pull-request jobs are limited to branches whose head repository is this same repository.
- Do not enable Docker socket or host-level privileged execution unless a future ADR explicitly approves it.

## Storage

This CI intentionally does not upload artifacts or dependency caches. Validation output remains in the GitHub job log and the same validation can be reproduced locally with `scripts/validate.py`.
