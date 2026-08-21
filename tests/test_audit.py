import json

from rea.audit import AuditLog


def test_audit_redacts_secrets(tmp_path) -> None:
    path = tmp_path / "audit.jsonl"
    AuditLog(path).write("test", actor="unit", data={"github_token": "abc", "safe": "ok"})
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["data"]["github_token"] == "***REDACTED***"
    assert record["data"]["safe"] == "ok"
