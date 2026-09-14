import json

from rea.audit import AuditLog


def test_audit_redacts_secrets(tmp_path) -> None:
    path = tmp_path / "audit.jsonl"
    AuditLog(path).write("test", actor="unit", data={"github_token": "abc", "safe": "ok"})
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["data"]["github_token"] == "***REDACTED***"
    assert record["data"]["safe"] == "ok"


def test_audit_records_have_stable_ids(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    log.write("one", actor="test")
    log.write("two", actor="test")
    records = [json.loads(line) for line in path.read_text("utf-8").splitlines()]
    assert records[0]["id"]
    assert records[0]["id"] != records[1]["id"]
