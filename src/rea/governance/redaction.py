from __future__ import annotations

import re
from typing import Any

_SECRET_PATTERNS = [
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        re.S,
    ),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
]
_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key|authorization)\b"
    r"\s*[:=]\s*['\"]?([^'\"\s,;]{8,})"
)


def redact_text(value: str) -> str:
    redacted = value
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("***REDACTED_SECRET***", redacted)
    return _ASSIGNMENT.sub(
        lambda match: f"{match.group(1)}=***REDACTED_SECRET***",
        redacted,
    )


def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {str(key): redact_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item) for item in value)
    return value


class StreamRedactor:
    """Buffers streamed model output and only ever releases text that has passed through
    `redact_text`.

    A naive token-by-token stream could flash an unredacted secret on screen before enough
    text has arrived to recognize the pattern (a PEM private key block spans many lines).
    This holds back anything from an unterminated "-----BEGIN...-----" marker onward until
    its closing marker shows up, so a secret is redacted as a whole before it is ever shown.
    """

    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, chunk: str) -> str:
        self._buffer += chunk
        begin_at = self._buffer.find("-----BEGIN")
        if begin_at != -1 and "-----END" not in self._buffer[begin_at:]:
            return ""  # holding an unterminated secret block; nothing is safe to emit yet

        cut = self._buffer.rfind("\n")
        if cut == -1:
            if len(self._buffer) < 160:
                return ""
            cut = len(self._buffer)
        else:
            cut += 1
        ready, self._buffer = self._buffer[:cut], self._buffer[cut:]
        return redact_text(ready) if ready else ""

    def finish(self) -> str:
        remainder = self._buffer
        self._buffer = ""
        return redact_text(remainder) if remainder else ""
