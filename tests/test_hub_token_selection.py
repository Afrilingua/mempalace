"""Credential selection for local clients of a live palace Hub."""

import json
import os

import pytest

from mempalace import server_registry


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("MEMPALACE_MCP_HTTP_TOKEN", raising=False)
    return tmp_path


def _register_live_hub(palace: str) -> dict:
    server_registry.write_serverinfo(
        palace,
        host="127.0.0.1",
        port=8765,
        scheme="http",
        read_only=False,
    )
    info = server_registry.read_live_serverinfo(palace)
    assert info is not None
    return info


def test_live_process_token_precedes_stale_palace_file(isolated_home, monkeypatch):
    palace = str(isolated_home / "palace")
    token_path = server_registry.server_token_path(palace)
    token_path.parent.mkdir(parents=True)
    token_path.write_text("stale-generated-token\n", encoding="utf-8")
    monkeypatch.setenv("MEMPALACE_MCP_HTTP_TOKEN", "current-explicit-token")

    info = _register_live_hub(palace)

    assert info["auth_token_source"] == "process"
    assert server_registry.load_server_tokens(palace) == (
        "current-explicit-token",
        "stale-generated-token",
    )
    assert server_registry.load_server_token(palace) == "current-explicit-token"


def test_live_palace_token_precedes_unrelated_process_token(isolated_home, monkeypatch):
    palace = str(isolated_home / "palace-b")
    token_path = server_registry.server_token_path(palace)
    token_path.parent.mkdir(parents=True)
    token_path.write_text("palace-b-token\n", encoding="utf-8")
    # The Hub starts with its own generated palace token.
    monkeypatch.setenv("MEMPALACE_MCP_HTTP_TOKEN", "palace-b-token")
    info = _register_live_hub(palace)
    # A later local caller may inherit an unrelated token for palace A.
    monkeypatch.setenv("MEMPALACE_MCP_HTTP_TOKEN", "palace-a-token")

    assert info["auth_token_source"] == "palace"
    assert server_registry.load_server_tokens(palace) == (
        "palace-b-token",
        "palace-a-token",
    )
    assert server_registry.load_server_token(palace) == "palace-b-token"


def test_live_unauthenticated_hub_ignores_stale_token_file(isolated_home, monkeypatch):
    palace = str(isolated_home / "palace")
    token_path = server_registry.server_token_path(palace)
    token_path.parent.mkdir(parents=True)
    token_path.write_text("old-token\n", encoding="utf-8")
    monkeypatch.delenv("MEMPALACE_MCP_HTTP_TOKEN", raising=False)

    info = _register_live_hub(palace)

    assert info["auth_token_source"] == "none"
    assert server_registry.load_server_tokens(palace) == ()
    assert server_registry.load_server_token(palace) == ""


def test_legacy_serverinfo_retains_palace_first_order(isolated_home, monkeypatch):
    palace = str(isolated_home / "palace")
    token_path = server_registry.server_token_path(palace)
    token_path.parent.mkdir(parents=True)
    token_path.write_text("palace-token\n", encoding="utf-8")
    monkeypatch.setenv("MEMPALACE_MCP_HTTP_TOKEN", "process-token")

    # A Hub from before auth_token_source was introduced remains discoverable.
    server_registry.serverinfo_path(palace).write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "host": "127.0.0.1",
                "port": 8765,
                "scheme": "http",
            }
        ),
        encoding="utf-8",
    )

    assert server_registry.load_server_tokens(palace) == (
        "palace-token",
        "process-token",
    )
    assert server_registry.load_server_token(palace) == "palace-token"
