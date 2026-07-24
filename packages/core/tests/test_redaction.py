"""Tests for the deterministic secret scrubber (§17.4 T2)."""

from __future__ import annotations

from time import perf_counter

from thalamus.core.redaction import RedactionEvent, RedactionResult, redact_secrets


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


def test_unclosed_private_key_markers_are_bounded() -> None:
    text = "-----BEGIN RSA PRIVATE KEY-----" * 8_000
    started = perf_counter()
    assert redact_secrets(text).text == text
    # The former DOTALL/non-greedy regex retried its suffix search at every begin marker.
    assert perf_counter() - started < 1.0


def test_env_assignment_redacts_value_keeps_key() -> None:
    result = redact_secrets("DB_PASSWORD=hunter2supersecret")
    assert "hunter2supersecret" not in result.text
    assert result.text == "DB_PASSWORD=[REDACTED:env-assignment]"


def test_env_assignment_handles_colon_and_quotes() -> None:
    result = redact_secrets('api_token: "abcdef1234567890"')
    assert "abcdef1234567890" not in result.text
    assert "[REDACTED:env-assignment]" in result.text
    assert result.text.startswith("api_token")


def test_env_assignment_handles_call_and_mapping_delimiters() -> None:
    text = "connect(password=hunter2secretval), {'api_token': 'abcdef1234567890'}"
    result = redact_secrets(text)
    assert "hunter2secretval" not in result.text
    assert "abcdef1234567890" not in result.text
    assert result.text.count("[REDACTED:env-assignment]") == 2
    assert result.text.endswith("'}")


def test_env_assignment_handles_multiline_and_comma_delimited_values() -> None:
    text = "PASSWORD=\ncorrecthorsebattery,\nTOKEN=anothersecret"
    result = redact_secrets(text)
    assert result.text == (
        "PASSWORD=\n[REDACTED:env-assignment],\nTOKEN=[REDACTED:env-assignment]"
    )


def test_nested_assignment_shapes_produce_one_intact_placeholder() -> None:
    result = redact_secrets("password=token=supersecretvalue")
    assert result.text == "password=[REDACTED:env-assignment]"
    assert result.events == (RedactionEvent("env-assignment", 1),)


def test_env_assignment_leaves_function_calls_and_short_values() -> None:
    # A function call (with parens) or a short value is not a secret.
    for benign in ("auth_token = get_token()", "secret = 42", "password = ok"):
        assert redact_secrets(benign).text == benign


def test_env_assignment_does_not_treat_bare_auth_prose_as_a_secret() -> None:
    text = "Analyzer taxonomy: auth=authentication and auth=authorization."
    assert redact_secrets(text).text == text


def test_explicit_and_compound_auth_assignments_are_redacted() -> None:
    text = (
        "authorization=bearercredential\n"
        "oauth_token=clientsecretvalue\n"
        "x_auth=proxycred\n"
        "session_auth: sessioncredential"
    )
    result = redact_secrets(text)
    assert result.text.count("[REDACTED:env-assignment]") == 4
    for secret in (
        "bearercredential",
        "clientsecretvalue",
        "proxycred",
        "sessioncredential",
    ):
        assert secret not in result.text


def test_explicit_legacy_auth_compounds_are_narrowly_redacted() -> None:
    text = (
        "auth_key=shortword\n"
        "auth_header=Vendor anotherword\n"
        "auth_data=opaque\n"
        "my_key=publicvalue\n"
        "session_key=publicvalue"
    )
    result = redact_secrets(text)
    assert result.text == (
        "auth_key=[REDACTED:env-assignment]\n"
        "auth_header=Vendor [REDACTED:env-assignment]\n"
        "auth_data=[REDACTED:env-assignment]\n"
        "my_key=publicvalue\n"
        "session_key=publicvalue"
    )


def test_auth_header_grammar_redacts_arbitrary_schemes_and_prefixes() -> None:
    cases = {
        "Authorization: Bearer 8f3a9c2e1b7d4056aa11": (
            "Authorization: Bearer [REDACTED:env-assignment]"
        ),
        "Authorization: APIKey 8f3a9c2e1b7d4056aa11": (
            "Authorization: APIKey [REDACTED:env-assignment]"
        ),
        "Authorization: NTLM TlRMTVNTUAABAAAAB4IIogAAAAAAAAAAAAAAAAAAAAAGAbEdAAAADw==": (
            "Authorization: NTLM [REDACTED:env-assignment]"
        ),
        "Authorization: SSWS 00QCjAl4MlV-WPXM2vK1": (
            "Authorization: SSWS [REDACTED:env-assignment]"
        ),
        "Authorization: Basic dXNlcjpwYXNzd29yZA==": (
            "Authorization: Basic [REDACTED:env-assignment]"
        ),
        "Proxy-Authorization: Vendor shortword": (
            "Proxy-Authorization: Vendor [REDACTED:env-assignment]"
        ),
        "X-Authorization: opaque": "X-Authorization: [REDACTED:env-assignment]",
        "HTTP_AUTHORIZATION=Hawk abc123": (
            "HTTP_AUTHORIZATION=Hawk [REDACTED:env-assignment]"
        ),
        "'Authorization': 'Custom abc123'": (
            "'Authorization': 'Custom [REDACTED:env-assignment]'"
        ),
        "curl -H 'Authorization: Hawk abc123'": (
            "curl -H 'Authorization: Hawk [REDACTED:env-assignment]'"
        ),
    }
    for text, expected in cases.items():
        redacted = redact_secrets(text).text
        assert redacted == expected
        assert redact_secrets(redacted).text == expected


def test_auth_field_policy_fails_toward_redaction_but_ignores_non_fields() -> None:
    assert redact_secrets("Authorization: required").text == (
        "Authorization: [REDACTED:env-assignment]"
    )
    for text in (
        "auth=authentication",
        "oauth=enabled",
        "oauth=2.0",
        "authorization_endpoint=https://accounts.google.com/o/oauth2/auth",
    ):
        assert redact_secrets(text).text == text


def test_auth_field_redaction_stops_at_quoted_and_line_boundaries() -> None:
    text = (
        "Authorization: Custom first-secret\n"
        "public=value\n"
        '"Proxy-Authorization": "Vendor second-secret"'
    )
    assert redact_secrets(text).text == (
        "Authorization: Custom [REDACTED:env-assignment]\n"
        "public=value\n"
        '"Proxy-Authorization": "Vendor [REDACTED:env-assignment]"'
    )


def test_auth_field_redacts_sigv4_and_folded_next_line_credentials() -> None:
    sigv4 = (
        "Authorization: AWS4-HMAC-SHA256 "
        "Credential=AKIDEXAMPLE/20260724/us-east-1/s3/aws4_request, "
        "SignedHeaders=host;x-amz-date, Signature=deadbeef"
    )
    assert redact_secrets(sigv4).text == (
        "Authorization: AWS4-HMAC-SHA256 [REDACTED:env-assignment]"
    )

    folded = (
        "Authorization: Vendor\r\n"
        "  opaque-token\r\n"
        "Public: value"
    )
    folded_redacted = (
        "Authorization: Vendor\r\n"
        "  [REDACTED:env-assignment]\r\n"
        "Public: value"
    )
    assert redact_secrets(folded).text == folded_redacted
    second_pass = redact_secrets(folded_redacted)
    assert second_pass.text == folded_redacted
    assert second_pass.events == ()

    next_line = "Authorization:\n  nextlinecredential\nPublic: value"
    assert redact_secrets(next_line).text == (
        "Authorization:\n  [REDACTED:env-assignment]\nPublic: value"
    )


def test_auth_dense_multi_edit_input_is_bounded() -> None:
    count = 30_000
    text = "Authorization: Vendor opaque-token\n" * count
    started = perf_counter()
    result = redact_secrets(text)
    elapsed = perf_counter() - started
    assert result.text.count("[REDACTED:env-assignment]") == count
    assert "opaque-token" not in result.text
    assert elapsed < 1.0


def test_env_assignment_scanner_handles_large_benign_finding() -> None:
    text = f"finding: {'a' * 100_000} auth=authentication"
    started = perf_counter()
    assert redact_secrets(text).text == text
    # The old basic-auth regex took ~56 seconds here. One second is deliberately generous for a
    # linear 100k scan while still making a quadratic regression unmistakably red.
    assert perf_counter() - started < 1.0


def test_url_basic_auth_password_is_redacted() -> None:
    result = redact_secrets("postgres://admin:s3cr3tP4ss@db.internal:5432/app")
    assert "s3cr3tP4ss" not in result.text
    assert "[REDACTED:basic-auth-url]" in result.text
    # structure preserved either side of the password
    assert result.text.startswith("postgres://admin:")
    assert result.text.endswith("@db.internal:5432/app")


def test_url_basic_auth_redacts_short_nonempty_password() -> None:
    result = redact_secrets("https://operator:x@internal.example/path")
    assert result.text == "https://operator:[REDACTED:basic-auth-url]@internal.example/path"


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


def test_opt_in_high_entropy_scanner_is_bounded() -> None:
    text = "a+" * 50_000
    started = perf_counter()
    assert redact_secrets(text, include_high_entropy=True).text == text
    assert perf_counter() - started < 1.0


def test_redaction_is_idempotent() -> None:
    once = redact_secrets("AWS=AKIAIOSFODNN7EXAMPLE token=anotherl0ngsecret").text
    twice = redact_secrets(once).text
    assert once == twice


def test_diceware_passphrase_redaction() -> None:
    result = redact_secrets("PASSWORD=correcthorsebattery")
    assert "correcthorsebattery" not in result.text
    assert "PASSWORD=[REDACTED:env-assignment]" in result.text
