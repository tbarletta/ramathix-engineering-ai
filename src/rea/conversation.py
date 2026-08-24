from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from .governance.redaction import StreamRedactor, redact_text, redact_value
from .initialization import render_project_analysis
from .models import ModelRouter

MAX_HISTORY_MESSAGES = 12
CHAT_MAX_TOKENS = 1536


class ConversationError(RuntimeError):
    pass


class ConversationalModel(Protocol):
    def chat_stream(
        self, *, model: str, messages: list[dict[str, str]], max_tokens: int = ...
    ) -> Iterator[str]: ...


@dataclass
class ConversationAssistant:
    router: ModelRouter
    model: ConversationalModel
    knowledge: dict[str, Any] | None = None
    history: list[dict[str, str]] = field(default_factory=list)

    def reply(
        self,
        message: str,
        *,
        pending_notice: str | None = None,
        on_token: Callable[[str], None] | None = None,
    ) -> str:
        safe_message = redact_text(message.strip())
        if not safe_message:
            raise ValueError("a message is required")

        if _requests_project_analysis(safe_message) and self.knowledge:
            response = render_project_analysis(self.knowledge)
            if on_token:
                on_token(response)
        else:
            target = self.router.resolve("engineering_manager")
            messages = [
                {"role": "system", "content": self._system_prompt(pending_notice)},
                *self.history[-MAX_HISTORY_MESSAGES:],
                {"role": "user", "content": safe_message},
            ]
            try:
                parts: list[str] = []
                redactor = StreamRedactor()
                for chunk in self.model.chat_stream(
                    model=target.model,
                    messages=messages,
                    max_tokens=CHAT_MAX_TOKENS,
                ):
                    parts.append(chunk)
                    if on_token:
                        safe_piece = redactor.feed(chunk)
                        if safe_piece:
                            on_token(safe_piece)
                if on_token:
                    tail = redactor.finish()
                    if tail:
                        on_token(tail)
                response = redact_text("".join(parts)).strip()
                if not response:
                    raise ValueError("Ollama returned an empty chat response")
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

    def remember(self, message: str, response: str) -> None:
        """Keep deterministic controller outcomes available to the next model turn."""
        self.history.extend(
            [
                {"role": "user", "content": redact_text(message.strip())},
                {"role": "assistant", "content": redact_text(response.strip())},
            ]
        )

    def _system_prompt(self, pending_notice: str | None = None) -> str:
        prompt = (
            "You are Ramathix Engineering AI, a local-first conversational engineering "
            "assistant. Always answer in Brazilian Portuguese, regardless of the user's "
            "language. Help clarify strategic goals, analyze "
            "engineering trade-offs, propose governed plans and explain REA capabilities. "
            "Never claim that you created issues, pull requests, deployments, purchases or "
            "production changes from a natural-language request alone. Never describe "
            "installing packages, editing files, writing config, or completing any step of a "
            "plan as already done — you have no tool access in this reply and nothing you "
            "write here changes any file. An action is real only when the governed session "
            "controller has returned its recorded result in the chat history after explicit "
            "approval. Cost and production changes remain subject to independent human gates. "
            "Be concise, candid about uncertainty, and ask for the repository or constraints "
            "when they are needed."
        )
        if pending_notice:
            prompt += "\n\n" + pending_notice
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
