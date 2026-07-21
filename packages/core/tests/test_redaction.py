"""Tests for the deterministic secret scrubber (§17.4 T2)."""

from __future__ import annotations

from thalamus.core.redaction import RedactionResult, redact_secrets


def _kinds(result: RedactionResult) -> set[str]:
    return {event.kind for event in result.events}


def test_empty_text_is_untouched() -> None:
    result = redact_secrets("")
    assert result.text == ""
    assert not result.redacted
    assert result.events == ()


def test_clean_prose_is_unchanged() -> None:
    text = "We chose centrality over usage because it ranks code-footprinted memories better."
    result = redact_secrets(text)
    assert result.text == text
    assert not result.redacted


def test_aws_access_key_is_redacted() -> None:
    result = redact_secrets("deploy with AKIAIOSFODNN7EXAMPLE in the env")
    assert "AKIAIOSFODNN7EXAMPLE" not in result.text
    assert "[REDACTED:aws-access-key]" in result.text
    assert _kinds(result) == {"aws-access-key"}


def test_github_token_is_redacted() -> None:
    token = "ghp_" + "a" * 36
    result = redact_secrets(f"export GH={token}")
    assert token not in result.text
    assert "[REDACTED:github-token]" in result.text


def test_openai_and_anthropic_keys_are_redacted() -> None:
    result = redact_secrets("keys: sk-abcdefghijklmnopqrstuvwx and sk-ant-abcdefghijklmnopqrstuvwx")
    assert "sk-abcdefghijklmnopqrstuvwx" not in result.text
    assert "sk-ant-abcdefghijklmnopqrstuvwx" not in result.text
    assert result.text.count("[REDACTED:api-key]") == 2


def test_jwt_is_redacted() -> None:
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dQw4w9WgXcQabc123"
    result = redact_secrets(f"Authorization: Bearer {jwt}")
    assert jwt not in result.text
    assert "[REDACTED:jwt]" in result.text


def test_private_key_block_is_redacted() -> None:
    pem = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEpAIBAAKCAQEA1234567890abcdef\nzzzz\n"
        "-----END RSA PRIVATE KEY-----"
    )
    result = redact_secrets(f"the key is:\n{pem}\nkeep it safe")
    assert "MIIEpAIBAAKCAQEA" not in result.text
    assert "[REDACTED:private-key]" in result.text
    assert result.text.startswith("the key is:")
    assert result.text.endswith("keep it safe")


def test_env_assignment_redacts_value_keeps_key() -> None:
    result = redact_secrets("DB_PASSWORD=hunter2supersecret")
    assert "hunter2supersecret" not in result.text
    assert result.text == "DB_PASSWORD=[REDACTED:env-assignment]"


def test_env_assignment_handles_colon_and_quotes() -> None:
    result = redact_secrets('api_token: "abcdef1234567890"')
    assert "abcdef1234567890" not in result.text
    assert "[REDACTED:env-assignment]" in result.text
    assert result.text.startswith("api_token")


def test_env_assignment_leaves_function_calls_and_short_values() -> None:
    # A function call or a short value is not a secret — don't mangle prose/pseudo-code.
    for benign in ("auth_token = get_token()", "secret = 42", "password = ok"):
        assert redact_secrets(benign).text == benign


def test_url_basic_auth_password_is_redacted() -> None:
    result = redact_secrets("postgres://admin:s3cr3tP4ss@db.internal:5432/app")
    assert "s3cr3tP4ss" not in result.text
    assert "[REDACTED:basic-auth-url]" in result.text
    # structure preserved either side of the password
    assert result.text.startswith("postgres://admin:")
    assert result.text.endswith("@db.internal:5432/app")


def test_multiple_secrets_are_counted_per_kind() -> None:
    text = "AKIAIOSFODNN7EXAMPLE AKIAIOSFODNN7EXAMPLF DB_PASSWORD=l0ngsecretvalue"
    result = redact_secrets(text)
    by_kind = {event.kind: event.count for event in result.events}
    assert by_kind["aws-access-key"] == 2
    assert by_kind["env-assignment"] == 1


def test_events_never_contain_the_secret() -> None:
    result = redact_secrets("token=supersecretvalue123")
    for event in result.events:
        assert "supersecretvalue123" not in event.kind


def test_high_entropy_is_off_by_default_but_opt_in() -> None:
    # A git sha (all-hex) must never be touched, even with the entropy sweep on.
    sha = "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4"
    assert redact_secrets(f"commit {sha}", include_high_entropy=True).text == f"commit {sha}"
    # A mixed-case base64-ish blob: untouched by default, removed when opted in.
    blob = "aGVsbG8Xd29ybGQ5SECRETtokenABCdef0123456789xyz"
    assert redact_secrets(blob).text == blob
    assert "[REDACTED:high-entropy]" in redact_secrets(blob, include_high_entropy=True).text


def test_redaction_is_idempotent() -> None:
    once = redact_secrets("AWS=AKIAIOSFODNN7EXAMPLE token=anotherl0ngsecret").text
    twice = redact_secrets(once).text
    assert once == twice
