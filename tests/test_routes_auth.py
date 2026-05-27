"""Tests for the bearer-auth dependency."""

import pytest
from fastapi import HTTPException

from health_metrics.routes.auth import get_principal


def test_missing_header_raises_401(monkeypatch):
    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    with pytest.raises(HTTPException) as exc:
        get_principal(authorization=None)
    assert exc.value.status_code == 401


def test_wrong_prefix_raises_401(monkeypatch):
    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    with pytest.raises(HTTPException) as exc:
        get_principal(authorization="Basic abc")
    assert exc.value.status_code == 401


def test_unknown_token_raises_401(monkeypatch):
    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    monkeypatch.setenv("HEALTH_API_TOKEN_MCP", "mcp-tok")
    with pytest.raises(HTTPException) as exc:
        get_principal(authorization="Bearer nope")
    assert exc.value.status_code == 401


def test_dashboard_token_returns_dashboard_principal(monkeypatch):
    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    monkeypatch.setenv("HEALTH_API_TOKEN_MCP", "mcp-tok")
    assert get_principal(authorization="Bearer dash-tok") == "dashboard"


def test_mcp_token_returns_mcp_principal(monkeypatch):
    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    monkeypatch.setenv("HEALTH_API_TOKEN_MCP", "mcp-tok")
    assert get_principal(authorization="Bearer mcp-tok") == "mcp"


def test_only_one_env_var_set_other_token_still_rejected(monkeypatch):
    monkeypatch.setenv("HEALTH_API_TOKEN_DASHBOARD", "dash-tok")
    monkeypatch.delenv("HEALTH_API_TOKEN_MCP", raising=False)
    with pytest.raises(HTTPException):
        get_principal(authorization="Bearer mcp-tok")
