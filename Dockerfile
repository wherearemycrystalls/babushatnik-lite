# Pinned manifest digests make rebuilds reproducible. Dependabot keeps them current.
FROM python:3.14-slim@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6

COPY --from=ghcr.io/astral-sh/uv:latest@sha256:62f8c047d0a0e9ece6b53fc63df902585a67a47a7f318ddec4a37db586edc8e3 /uv /usr/local/bin/uv

WORKDIR /app

# uv keeps the container environment outside /app so local bind mounts cannot replace it.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
ENV UV_LINK_MODE=copy
ENV UV_PYTHON_DOWNLOADS=never
ENV UV_CACHE_DIR=/tmp/uv-cache
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Copy only the lock files first; source code changes won't bust this layer.
COPY pyproject.toml uv.lock .python-version ./

RUN --mount=type=cache,target=/tmp/uv-cache \
    uv sync --locked --no-dev --no-install-project

COPY . .

RUN --mount=type=cache,target=/tmp/uv-cache \
    uv sync --locked --no-dev

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin app \
    && install -d -o app -g app /var/lib/celery

USER app

# Dependencies are installed at build time; startup also works offline and read-only.
ENTRYPOINT ["uv", "run", "--no-sync", "--no-dev"]
CMD ["python", "-m", "bot.main"]
