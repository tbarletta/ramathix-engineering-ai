from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from .governance.redaction import redact_text, redact_value
from .initialization import render_project_analysis
from .models import ModelRouter

MAX_HISTORY_MESSAGES = 12


class ConversationError(RuntimeError):
    pass


class ConversationalModel(Protocol):
    def chat(self, *, model: str, messages: list[dict[str, str]]) -> str: ...


@dataclass
class ConversationAssistant:
    router: ModelRouter
    model: ConversationalModel
    knowledge: dict[str, Any] | None = None
    history: list[dict[str, str]] = field(default_factory=list)

    def reply(self, message: str) -> str:
        safe_message = redact_text(message.strip())
        if not safe_message:
            raise ValueError("a message is required")

        if _requests_project_analysis(safe_message) and self.knowledge:
            response = render_project_analysis(self.knowledge)
        else:
            target = self.router.resolve("engineering_manager")
            messages = [
                {"role": "system", "content": self._system_prompt()},
                *self.history[-MAX_HISTORY_MESSAGES:],
                {"role": "user", "content": safe_message},
            ]
            try:
                response = redact_text(self.model.chat(model=target.model, messages=messages))
            except httpx.HTTPError as exc:
                raise ConversationError(
                    "O Ollama não está disponível. Inicie o serviço local e verifique "
                    "`rea models status`."
                ) from exc
            except ValueError as exc:
                raise ConversationError(
                    "O modelo local não retornou uma resposta utilizável."
                ) from exc

        self.history.extend(
            [
                {"role": "user", "content": safe_message},
                {"role": "assistant", "content": response},
            ]
        )
        return response

    def _system_prompt(self) -> str:
        prompt = (
            "You are Ramathix Engineering AI, a local-first conversational engineering "
            "assistant. Always answer in Brazilian Portuguese, regardless of the user's "
            "language. Help clarify strategic goals, analyze "
            "engineering trade-offs, propose governed plans and explain REA capabilities. "
            "Never claim that you created issues, pull requests, deployments, purchases or "
            "production changes from this chat. Those actions remain subject to explicit REA "
            "governance and human approval. Be concise, candid about uncertainty, and ask for "
            "the repository or constraints when they are needed."
        )
        if self.knowledge:
            prompt += (
                "\n\nUse this repository inventory as factual context. Treat its confidence "
                "labels as authoritative and do not invent repository details:\n"
                + json.dumps(redact_value(self.knowledge), ensure_ascii=False, indent=2)
            )
        return prompt


def _requests_project_analysis(message: str) -> bool:
    normalized = message.lower()
    return any(
        phrase in normalized
        for phrase in (
            "análise do projeto",
            "analise do projeto",
            "analisar o projeto",
            "análise técnica",
        )
    )
