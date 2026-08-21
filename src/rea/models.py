from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import yaml

from .domain import ModelTarget


class ModelRouter:
    def __init__(self, models: dict[str, dict[str, Any]], routes: dict[str, str]) -> None:
        self.models = models
        self.routes = routes

    @classmethod
    def from_yaml(cls, path: Path) -> "ModelRouter":
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls(models=data.get("models", {}), routes=data.get("routes", {}))

    def resolve(self, role: str) -> ModelTarget:
        alias = self.routes.get(role)
        if alias is None:
            raise KeyError(f"no model route configured for role: {role}")
        config = self.models[alias]
        return ModelTarget(
            alias=alias,
            provider=config["provider"],
            model=config["model"],
            context_window=int(config.get("context_window", 8192)),
        )


class OllamaClient:
    def __init__(self, base_url: str, timeout: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def list_models(self) -> set[str]:
        response = httpx.get(f"{self.base_url}/api/tags", timeout=10.0)
        response.raise_for_status()
        return {item["name"] for item in response.json().get("models", [])}

    def chat_json(
        self, *, model: str, system: str, user: str, schema: dict[str, Any]
    ) -> dict[str, Any]:
        payload = {
            "model": model,
            "stream": False,
            "format": schema,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        response = httpx.post(f"{self.base_url}/api/chat", json=payload, timeout=self.timeout)
        response.raise_for_status()
        content = response.json()["message"]["content"]
        return json.loads(content)
