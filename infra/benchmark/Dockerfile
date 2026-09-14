FROM python:3.12.11-slim

RUN python -m pip install --no-cache-dir \
    httpx==0.28.1 \
    PyYAML==6.0.2 \
    typer==0.16.0 \
    pytest==8.4.1 \
    ruff==0.12.7

ENV PYTHONDONTWRITEBYTECODE=1
WORKDIR /workspace
