"""Bearer-token auth for /api/v1/* routes. Two principals (dashboard + MCP)
so the audit log can distinguish callers (spec §5)."""

import os
from typing import Literal

from fastapi import Header, HTTPException

Principal = Literal["dashboard", "mcp"]


def get_principal(authorization: str | None = Header(default=None)) -> Principal:
    """Resolve the caller's principal from the Authorization header.

    Tokens come from env vars:
      - HEALTH_API_TOKEN_DASHBOARD
      - HEALTH_API_TOKEN_MCP

    Raises 401 if missing or unrecognized.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    dashboard = os.environ.get("HEALTH_API_TOKEN_DASHBOARD")
    mcp = os.environ.get("HEALTH_API_TOKEN_MCP")
    if dashboard and token == dashboard:
        return "dashboard"
    if mcp and token == mcp:
        return "mcp"
    raise HTTPException(status_code=401, detail="Invalid bearer token")
