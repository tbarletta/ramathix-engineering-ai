from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import httpx

from .governance.redaction import redact_text
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
    history: list[dict[str, str]] = field(default_factory=list)

    def reply(self, message: str) -> str:
        safe_message = redact_text(message.strip())
        if not safe_message:
            raise ValueError("a message is required")

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
                "Ollama is unavailable. Start the local service and verify `rea models status`."
            ) from exc
        except ValueError as exc:
            raise ConversationError("The local model did not return a usable response.") from exc

        self.history.extend(
            [
                {"role": "user", "content": safe_message},
                {"role": "assistant", "content": response},
            ]
        )
        return response

    @staticmethod
    def _system_prompt() -> str:
        return (
            "You are Ramathix Engineering AI, a local-first conversational engineering "
            "assistant. Answer in the user's language. Help clarify strategic goals, analyze "
            "engineering trade-offs, propose governed plans and explain REA capabilities. "
            "Never claim that you created issues, pull requests, deployments, purchases or "
            "production changes from this chat. Those actions remain subject to explicit REA "
            "governance and human approval. Be concise, candid about uncertainty, and ask for "
            "the repository or constraints when they are needed."
        )
