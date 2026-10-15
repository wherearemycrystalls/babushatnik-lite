# Development and delivery

## Python and dependencies

Use [uv](https://docs.astral.sh/uv/) for Python, dependencies and every local
project command. On macOS, install it with `brew install uv`; other platforms
can use the [official installer](https://docs.astral.sh/uv/getting-started/installation/).

The Python version is selected by `.python-version`. Install it and synchronize
the development environment from the committed lockfile:

```bash
uv python install
uv sync --locked --group dev
```

This is also available as `make install`. uv creates and manages `.venv`
automatically. Do not copy it between machines; recreate it with the commands
above when the OS or architecture changes. Run commands through `uv run`
without activating the environment.

Manage dependency changes through uv and commit both `pyproject.toml` and
`uv.lock`:

```bash
uv add package-name
uv add --group dev package-name
uv remove package-name
uv lock --upgrade-package package-name
```

## Local checks

After installing the environment, run the same Python checks used by CI:

```bash
make check
```

This runs Ruff, Python bytecode compilation, the dependency audit and tests.
Individual checks use `uv run --locked ruff format --check .`,
`uv run --locked ruff check .`, `uv run --locked pip-audit`, or
`uv run --locked python -m unittest discover -s tests -v`.
`--locked` fails if project metadata and the lockfile differ.

For the PostgreSQL integration tests, start the database and set
`TEST_DATABASE_URL`; CI does this automatically. Validate Compose and build the
same immutable application image locally with:

```bash
make compose-config
docker build -t babushatnik-lite:local .
```

## Running services

Configure `.env` from `.env.example`. Local processes need PostgreSQL and Redis
addresses reachable from the host; Docker Compose supplies the container
addresses automatically.

Use `make migrate` and `make bootstrap` to prepare the database, then run the
required services in separate terminals with `make admin`, `make bot`,
`make worker-scraper`, `make worker-bot`, and `make beat`. Every target uses
`uv run --locked`. For example:

```bash
uv run --locked python -m bot.main
uv run --locked celery -A background.celery_app:celery_app worker -Q scraper --concurrency=1 --loglevel=INFO
```

For a complete container stack, use `make local-up`. The image
installs dependencies with `uv sync --locked --no-dev`, and its entrypoint runs
all service commands through `uv run --no-sync --no-dev`. The container
environment lives at `/opt/venv`, separate from the host's `.venv`; runtime
startup does not download packages or change the installed environment.
Only source directories/files are mounted into local containers, read-only.
See [docs/COMPOSE.md](docs/COMPOSE.md) for ports, environment selection,
worker reload, persistent volumes and production operations.

## Pull requests

Keep each change focused. A pull request must pass **Workflow and shell checks**,
**Quality**, **Tests (PostgreSQL)**, **Container and Compose**, and the relevant
security checks. Do not include `.env`, database dumps, generated model
artefacts or credentials.

Release and production procedures are in [docs/CICD.md](docs/CICD.md).
