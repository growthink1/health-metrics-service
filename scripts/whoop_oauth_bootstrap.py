"""
One-time Whoop OAuth bootstrap.

Run this once with WHOOP_CLIENT_ID + WHOOP_CLIENT_SECRET set in .env (or
exported in the environment). It prints an authorization URL — open in a
browser, authorize, and paste the redirected `code` query param back. It
exchanges the code for access + refresh tokens, then prints the refresh
token in a form you can paste into .env.

Reads config via pydantic-settings (same as the rest of the service) so
the .env file is picked up automatically.

DO NOT run unless you've configured a Whoop developer app.
"""

import asyncio
import sys
from pathlib import Path
from urllib.parse import urlencode

import httpx

# Make `src/health_metrics` importable when run from the repo root.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from health_metrics.config import get_settings  # noqa: E402

AUTH_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
SCOPES = [
    "read:cycles",
    "read:recovery",
    "read:sleep",
    "read:workout",
    "read:profile",
    "offline",
]


def _require(name: str, value: str | None) -> str:
    if not value:
        print(
            f"ERROR: {name} is not set. Add it to .env or export it before running this script.",
            file=sys.stderr,
        )
        sys.exit(2)
    return value


async def main() -> int:
    settings = get_settings()
    client_id = _require("WHOOP_CLIENT_ID", settings.whoop_client_id)
    client_secret = _require("WHOOP_CLIENT_SECRET", settings.whoop_client_secret)
    redirect_uri = settings.whoop_redirect_uri
    token_url = settings.whoop_oauth_url

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(SCOPES),
        "state": "bootstrap",
    }
    print(
        "Open this URL, authorize, then paste back the `code` query param "
        f"from the redirect URL:\n\n{AUTH_URL}?{urlencode(params)}\n"
    )
    code = input("code: ").strip()

    async with httpx.AsyncClient(timeout=30.0) as http:
        resp = await http.post(
            token_url,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )
        if resp.status_code != 200:
            print(
                f"\nERROR: token endpoint returned {resp.status_code}\n{resp.text}",
                file=sys.stderr,
            )
            return 1
        body = resp.json()

    print("\nSuccess. Replace the WHOOP_REFRESH_TOKEN line in .env with:\n")
    print(f"WHOOP_REFRESH_TOKEN={body['refresh_token']}")
    print(
        f"\n(Access token, for reference — expires in {body.get('expires_in')}s; "
        "the service will refresh automatically on first use):"
    )
    print(body["access_token"])
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
