from rea.governance.redaction import StreamRedactor, redact_text


def test_redact_text_masks_known_secret_patterns() -> None:
    assert "***REDACTED_SECRET***" in redact_text("token: ghp_" + "a" * 30)
    assert "AKIA" not in redact_text("key AKIA1234567890ABCDEF")


def test_stream_redactor_emits_ordinary_text_as_it_arrives() -> None:
    redactor = StreamRedactor()
    out = redactor.feed("Hello ")
    out += redactor.feed("world, this line is long enough to flush on its own merits.\n")
    out += redactor.feed("Second line without a trailing newline")
    out += redactor.finish()

    assert "Hello world" in out
    assert "Second line" in out


def test_stream_redactor_holds_back_an_unterminated_secret_block() -> None:
    redactor = StreamRedactor()

    held = redactor.feed("Some text\n-----BEGIN RSA PRIVATE KEY-----\nMIIB")
    assert "-----BEGIN" not in held
    assert "MIIB" not in held

    held += redactor.feed("more-key-bytes")
    assert "more-key-bytes" not in held

    released = redactor.feed("-----END RSA PRIVATE KEY-----\nmore text after\n")

    assert "-----BEGIN" not in released
    assert "MIIB" not in released
    assert "***REDACTED_SECRET***" in released
    assert "more text after" in released


def test_stream_redactor_never_shows_an_unterminated_block_early() -> None:
    """redact_text can only redact a *complete* BEGIN...END block (same as non-streaming
    mode) — the safety property streaming adds is that an incomplete block is never shown
    progressively while it's still forming, not that an unterminated one gets redacted."""
    redactor = StreamRedactor()

    held = redactor.feed("-----BEGIN PRIVATE KEY-----\nsecret-bytes-never-closed")
    assert held == ""

    tail = redactor.finish()

    # Same content redact_text() would receive in the non-streaming path — no regression,
    # just never displayed piecemeal while its end marker hadn't arrived yet.
    assert tail == redact_text("-----BEGIN PRIVATE KEY-----\nsecret-bytes-never-closed")
