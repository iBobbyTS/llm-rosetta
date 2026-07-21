"""Tests for targeted diagnostic secret redaction."""

import json

import pytest

from codex_rosetta.observability.redaction import (
    REDACTED,
    SecretRedactor,
    collect_token_values,
)


def test_collects_only_configured_api_tokens():
    values = collect_token_values(
        {
            "providers": {"p": {"api_key": "provider-secret"}},
            "server": {
                "admin_password": "admin-secret",
                "api_keys": [{"id": "client", "key": "gateway-secret"}],
                "proxy": "http://user:proxy-secret@example.test:8080",
            },
            "oauth": {"access_token": "oauth-token", "client_secret": "keep-me"},
        }
    )
    assert values == {
        "provider-secret",
        "gateway-secret",
        "oauth-token",
    }


def test_redacts_only_tokens_and_preserves_non_token_payload_data():
    redactor = SecretRedactor({"known-api-token"})
    value = {
        "prompt": "Email alice@example.com and keep source: token = user_value",
        "source": "def bearer(value): return value",
        "api_key": "field-secret",
        "password": "password-secret",
        "secret": "ordinary-secret",
        "client_secret": "client-secret",
        "proxy_password": "proxy-secret",
        "token_count": 123,
        "max_tokens": 4096,
        "nested": {
            "Authorization": "Bearer bearer-secret",
            "ordinary": "prefix known-api-token suffix",
        },
    }

    redacted = redactor.redact(value)

    assert redacted["prompt"] == value["prompt"]
    assert redacted["source"] == value["source"]
    assert redacted["api_key"] == REDACTED
    assert redacted["password"] == "password-secret"
    assert redacted["secret"] == "ordinary-secret"
    assert redacted["client_secret"] == "client-secret"
    assert redacted["proxy_password"] == "proxy-secret"
    assert redacted["token_count"] == 123
    assert redacted["max_tokens"] == 4096
    assert redacted["nested"]["Authorization"] == REDACTED
    assert redacted["nested"]["ordinary"] == "prefix [REDACTED] suffix"


def test_redacts_token_fields_inside_encoded_tool_arguments_only():
    redactor = SecretRedactor({"known-api-token"})
    arguments = json.dumps(
        {
            "command": (
                "curl -H 'Authorization: Bearer bearer-secret' "
                "https://user@example.com?key=known-api-token"
            ),
            "api_key": "tool-api-key",
            "password": "ordinary-password",
            "secret": "ordinary-secret",
            "client_secret": "ordinary-client-secret",
            "proxy_password": "ordinary-proxy-password",
            "prompt": "keep user@example.com and the rest of this prompt",
        },
        separators=(",", ":"),
    )
    tool_call = {
        "id": "call_1",
        "type": "function",
        "function": {"name": "Bash", "arguments": arguments},
    }

    redacted = redactor.redact(tool_call)
    redacted_arguments = json.loads(redacted["function"]["arguments"])

    assert "bearer-secret" not in redacted_arguments["command"]
    assert "known-api-token" not in redacted_arguments["command"]
    assert redacted_arguments["api_key"] == REDACTED
    assert redacted_arguments["password"] == "ordinary-password"
    assert redacted_arguments["secret"] == "ordinary-secret"
    assert redacted_arguments["client_secret"] == "ordinary-client-secret"
    assert redacted_arguments["proxy_password"] == "ordinary-proxy-password"
    assert redacted_arguments["prompt"] == (
        "keep user@example.com and the rest of this prompt"
    )


def test_keeps_encoded_tool_arguments_byte_identical_without_tokens():
    arguments = '{"password":"ordinary-password","prompt":"user@example.com"}'
    tool_call = {
        "id": "call_1",
        "type": "function",
        "function": {"name": "Bash", "arguments": arguments},
    }

    redacted = SecretRedactor().redact(tool_call)

    assert redacted["function"]["arguments"] == arguments


def test_exact_redaction_preserves_non_secret_fields_and_nested_shapes():
    redactor = SecretRedactor({"provider-secret"})
    value = {
        "token": "ordinary-model-value",
        "nested": [
            "before provider-secret after",
            {"blob": b"\xffprovider-secret\x00"},
        ],
    }

    redacted = redactor.redact_exact(value)

    assert redacted == {
        "token": "ordinary-model-value",
        "nested": [
            "before [REDACTED] after",
            {"blob": b"\xff[REDACTED]\x00"},
        ],
    }
    assert value["nested"][0] == "before provider-secret after"


@pytest.mark.parametrize("method_name", ["redact", "redact_exact"])
def test_configured_tokens_are_redacted_from_dict_keys_with_last_item_wins(
    method_name: str,
) -> None:
    token = "provider-key-secret"
    value = {
        token: "secret-key-first",
        REDACTED: "ordinary-later-value",
        f"prefix-{token}": "string-key",
        f"bytes-{token}".encode(): "bytes-key",
        7: "non-string-key",
    }

    redacted = getattr(SecretRedactor({token}), method_name)(value)

    assert token not in repr(redacted)
    assert redacted[REDACTED] == "ordinary-later-value"
    assert redacted["prefix-[REDACTED]"] == "string-key"
    assert redacted[b"bytes-[REDACTED]"] == "bytes-key"
    assert redacted[7] == "non-string-key"


def test_wire_redaction_handles_json_escaped_credentials():
    token = 'provider-"quoted\\key-\N{SNOWMAN}'
    payload = json.dumps(
        {"nested": {"credential": token}},
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode()

    redacted = SecretRedactor({token}).redact_wire_bytes(payload)

    assert token.encode() not in redacted
    assert json.loads(redacted)["nested"]["credential"] == "[REDACTED]"


def test_streaming_wire_redaction_covers_every_token_split_and_prefix_overlap():
    token = b"provider-secret-long"
    payload = b'event: message\ndata: {"text":"before ' + token + b' after"}\n\n'
    expected = payload.replace(token, b"[REDACTED]")
    redactor = SecretRedactor({"provider-secret", token.decode()})

    for split in range(len(payload) + 1):
        stream = redactor.streaming_redactor()
        actual = stream.feed(payload[:split])
        actual += stream.feed(payload[split:])
        actual += stream.finish()
        assert actual == expected

    bytewise = redactor.streaming_redactor()
    actual = b"".join(bytewise.feed(bytes([value])) for value in payload)
    actual += bytewise.finish()
    assert actual == expected
