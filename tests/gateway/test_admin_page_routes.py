"""Tests for admin page URL routes."""

from __future__ import annotations

import asyncio
import html as html_module
import json
import re

import pytest

from codex_rosetta._vendor.httpserver import Request
from codex_rosetta.gateway.app import create_app
from codex_rosetta.gateway.admin.static import load_admin_html
from codex_rosetta.gateway.config import GatewayConfig


def _make_app():
    config = GatewayConfig(
        {
            "providers": {
                "test-provider": {
                    "api_key": "sk-test",
                    "base_url": "https://api.example.test/v1",
                    "type": "openai",
                }
            },
            "model_groups": {
                "test": {
                    "provider": "test-provider",
                    "type": "llm",
                    "models": {"gpt-test": {}},
                }
            },
            "server": {
                "admin_password": "test-admin-password",
                "api_keys": [
                    {
                        "id": "test-client",
                        "label": "Test client",
                        "key": "test-gateway-key",
                    }
                ],
            },
        }
    )
    return create_app(config)


def _request(app, path: str) -> Request:
    return Request(
        method="GET",
        path=path,
        query_string="",
        headers={},
        body=b"",
        client_addr=("127.0.0.1", 12345),
        app=app,
    )


@pytest.mark.parametrize(
    "path",
    [
        "/admin",
        "/admin/",
        "/admin/providers",
        "/admin/providers/",
        "/admin/models",
        "/admin/keys",
        "/admin/keys/",
        "/admin/tools",
        "/admin/tools/",
        "/admin/dashboard",
        "/admin/logs",
        "/admin/gateway-logs",
    ],
)
def test_admin_page_routes_serve_admin_html(path: str):
    app = _make_app()

    response = asyncio.run(app._dispatch(_request(app, path)))

    assert response.status_code == 200
    assert response.headers["Content-Type"] == "text/html; charset=utf-8"
    assert response.headers["Content-Security-Policy"] == "frame-ancestors 'none'"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert b'class="admin-nav"' in response.body


def test_admin_login_token_has_no_browser_inactivity_expiry() -> None:
    admin_html = load_admin_html()

    assert "localStorage.setItem('admin_token', data.token);" in admin_html
    assert "localStorage.removeItem('admin_token');" in admin_html
    assert "INACTIVITY_TIMEOUT_MS" not in admin_html
    assert "_startInactivityTracking" not in admin_html


def test_admin_dynamic_handlers_and_attributes_use_context_specific_encoding():
    """Malicious persisted names cannot break handler or attribute boundaries."""
    admin_html = load_admin_html()
    payload = "x');globalThis.__pwned=1;//\"<&"

    # handlerArg's contract is JSON string serialization followed by HTML
    # attribute encoding.  Browser entity decoding must reconstruct one JSON
    # string literal whose value is exactly the attacker-controlled name.
    encoded_arg = html_module.escape(json.dumps(payload), quote=True)
    assert json.loads(html_module.unescape(encoded_arg)) == payload
    assert "return escAttr(JSON.stringify(String(value)));" in admin_html

    unsafe_handlers = re.findall(
        r'on(?:click|change)="[^"\n]*\$\{esc\(',
        admin_html,
    )
    assert unsafe_handlers == []

    unsafe_attributes = re.findall(
        r'(?:aria-controls|data-[\w-]+|id|src|title|value)="[^"\n]*\$\{esc\(',
        admin_html,
    )
    assert unsafe_attributes == []


def test_admin_model_test_metadata_uses_dom_api_not_html_strings():
    """Provider result metadata stays on the safe-by-construction DOM path."""
    admin_html = load_admin_html()
    run_test_source = admin_html.split("async function runTest", 1)[1].split(
        "// ===================== Init", 1
    )[0]

    assert "Number.isSafeInteger(value)" in admin_html
    assert "meta.innerHTML" not in run_test_source
    assert "_appendTestUsageMeta(meta, type, body?.usage);" in run_test_source
    assert "meta.replaceChildren(_createTestMetaItem('Model', model));" in admin_html
