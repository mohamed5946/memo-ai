"""Security tests: redaction, invite gate, owner code, backup.

The invite code VALUE never appears in any test — only the hash
lives in source. The 'ok' path is proven on Windows by entering the
real code by hand (field test), not here.
"""

from lona2 import backup, invite, logger, owner


def test_redaction_removes_bearer_token_entirely():
    token = "x7Qw9zR2kL8mN4pT6vB1yH3jF5dS0aZ"
    line = f"Authorization: Bearer {token} and then some text"
    out = logger.redact(line)
    assert token not in out
    assert "Bearer" in out  # the scheme word may stay; the token must go
    assert "[REDACTED]" in out


def test_redaction_removes_api_keys():
    groq = "gsk_abcdefghij1234567890ABCDEFGH"
    gemini = "AIzaSyAbCdEfGhIjKlMnOpQrStUvWxYz123456"
    out = logger.redact(f"key={groq} other={gemini}")
    assert groq not in out
    assert gemini not in out
    assert "gsk_" not in out
    assert "AIza" not in out


def test_redaction_leaves_normal_text_alone():
    text = "LONA started - main window only, temp 51C, battery 80%"
    assert logger.redact(text) == text


def test_log_lines_are_redacted():
    token = "sekritTOKEN1234567890"
    logger.error("test", f"call failed, Authorization: Bearer {token}")
    lines = logger.get_lines(5)
    assert lines
    assert token not in "\n".join(lines)


def test_invite_rejects_wrong_format():
    assert invite.verify("123") == "format"
    assert invite.verify("abcdef") == "format"
    assert invite.verify("") == "format"
    assert invite.verify(None) == "format"


def test_invite_rejects_wrong_code():
    invite.reset_lockout_for_tests()
    assert invite.verify("000000") == "invalid"
    assert invite.verify("999999") == "invalid"


def test_invite_arabic_digits_translate():
    invite.reset_lockout_for_tests()
    # Arabic-Indic digits must reach the checker as ASCII, then fail
    # as a wrong code (not as a format error).
    assert invite.verify("٠٠٠٠٠٠") == "invalid"


def test_invite_locks_after_five_failures():
    invite.reset_lockout_for_tests()
    for _ in range(5):
        assert invite.verify("111111") == "invalid"
    assert invite.verify("111111") == "locked"
    invite.reset_lockout_for_tests()
    assert invite.verify("111111") == "invalid"


def test_invite_hash_is_the_only_code_artifact():
    import pathlib
    import re
    from lona2 import invite as invite_mod
    # The two string literals in source must join to exactly the hash.
    assert invite_mod.INVITE_CODE_HASH == (
        "ce0e90a75246a53c921aaf19e18f179e2f243c6442214b34ffb6091cc5e6bf03")
    src = pathlib.Path(__file__).resolve().parents[1] / "invite.py"
    text = src.read_text(encoding="utf-8")
    # No 6-digit string literal (a plaintext code) may sit in the file.
    assert re.search(r'"\d{6}"', text) is None
    assert re.search(r"'\d{6}'", text) is None


def test_owner_code_roundtrip():
    assert owner.set_owner_code("123456")["ok"] is True
    assert owner.check_owner_code("123456") is True
    assert owner.check_owner_code("654321") is False
    assert owner.has_owner_code() is True


def test_owner_code_rejects_bad_format():
    assert owner.set_owner_code("123")["ok"] is False
    assert owner.set_owner_code("abcdef")["ok"] is False


def test_backup_roundtrip():
    payload = {"settings": {"ai_name": "لونا"}, "tasks": [{"id": "1"}]}
    exported = backup.export_backup("secret1", payload)
    assert exported["ok"] is True
    imported = backup.import_backup("secret1", exported["data"])
    assert imported["ok"] is True
    assert imported["payload"] == payload


def test_backup_wrong_password_fails():
    exported = backup.export_backup("secret1", {"a": 1})
    assert backup.import_backup("wrongpass", exported["data"])["ok"] is False


def test_backup_tampered_blob_fails():
    exported = backup.export_backup("secret1", {"a": 1})
    blob = exported["data"]
    tampered = blob[:-4] + ("AAAA" if blob[-4:] != "AAAA" else "BBBB")
    assert backup.import_backup("secret1", tampered)["ok"] is False


def test_backup_short_password_rejected():
    assert backup.export_backup("abc", {"a": 1})["ok"] is False
