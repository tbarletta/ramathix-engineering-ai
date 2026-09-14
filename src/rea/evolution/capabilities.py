from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from uuid import uuid4


class CapabilityDenied(PermissionError):
    pass


class CapabilityBroker:
    """Emite capacidades curtas e vinculadas a uma versão específica de skill."""

    def __init__(self, key_path: Path, *, max_ttl_seconds: int = 300) -> None:
        self.key_path = key_path
        self.max_ttl_seconds = max_ttl_seconds

    def issue(
        self,
        *,
        skill_id: str,
        version: str,
        capabilities: set[str],
        approved_capabilities: set[str],
        ttl_seconds: int = 60,
    ) -> str:
        missing = capabilities.difference(approved_capabilities)
        if missing:
            raise CapabilityDenied(
                "capacidades não aprovadas: " + ", ".join(sorted(missing))
            )
        if not 1 <= ttl_seconds <= self.max_ttl_seconds:
            raise ValueError("validade da capacidade fora do limite")
        payload = {
            "skill": skill_id,
            "version": version,
            "capabilities": sorted(capabilities),
            "expires_at": int(time.time()) + ttl_seconds,
            "nonce": uuid4().hex,
        }
        encoded = self._encode(payload)
        signature = hmac.new(self._key(), encoded, hashlib.sha256).digest()
        return f"{self._urlsafe(encoded)}.{self._urlsafe(signature)}"

    def verify(
        self,
        token: str,
        *,
        skill_id: str,
        version: str,
        required_capabilities: set[str],
    ) -> set[str]:
        try:
            payload_part, signature_part = token.split(".", 1)
            encoded = self._decode(payload_part)
            signature = self._decode(signature_part)
            expected = hmac.new(self._key(), encoded, hashlib.sha256).digest()
            if not hmac.compare_digest(signature, expected):
                raise CapabilityDenied("assinatura de capacidade inválida")
            payload = json.loads(encoded)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise CapabilityDenied("token de capacidade inválido") from exc
        if payload.get("skill") != skill_id or payload.get("version") != version:
            raise CapabilityDenied("capacidade vinculada a outra skill ou versão")
        if int(payload.get("expires_at", 0)) < int(time.time()):
            raise CapabilityDenied("capacidade expirada")
        granted = set(payload.get("capabilities", []))
        missing = required_capabilities.difference(granted)
        if missing:
            raise CapabilityDenied(
                "capacidades ausentes: " + ", ".join(sorted(missing))
            )
        return granted

    def _key(self) -> bytes:
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.key_path.exists():
            descriptor = os.open(
                self.key_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
            try:
                os.write(descriptor, os.urandom(32))
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        key = self.key_path.read_bytes()
        if len(key) < 32:
            raise RuntimeError("chave do broker de capacidades inválida")
        return key

    @staticmethod
    def _encode(payload: dict[str, object]) -> bytes:
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

    @staticmethod
    def _urlsafe(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode().rstrip("=")

    @staticmethod
    def _decode(value: str) -> bytes:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
