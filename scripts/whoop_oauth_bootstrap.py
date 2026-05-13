"""
One-time Whoop OAuth bootstrap.

Run this once with WHOOP_CLIENT_ID + WHOOP_CLIENT_SECRET set. It prints
an authorization URL — open it in a browser, authorize, and paste the
redirected `code` query param back into this script. It will then
exchange the code for access + refresh tokens, and write the refresh
token to .env.

DO NOT run unless you've actually configured a Whoop developer app.
"""

import asyncio
import os
import sys
from urllib.parse import urlencode

import httpx

CLIENT_ID = os.environ["WHOOP_CLIENT_ID"]
CLIENT_SECRET = os.environ["WHOOP_CLIENT_SECRET"]
REDIRECT_URI = os.environ.get(
    "WHOOP_REDIRECT_URI", "http://localhost:8000/whoop/callback"
)
AUTH_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
SCOPES = [
    "read:cycles",
    "read:recovery",
    "read:sleep",
    "read:workout",
    "read:profile",
    "offline",
]


async def main() -> int:
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": " ".join(SCOPES),
        "state": "bootstrap",
    }
    print(f"Open this URL, authorize, then paste back the `code` query param:\n\n{AUTH_URL}?{urlencode(params)}\n")
    code = input("code: ").strip()

    async with httpx.AsyncClient(timeout=30.0) as http:
        resp = await http.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
            },
        )
        resp.raise_for_status()
        body = resp.json()

    print("\nSuccess. Add this to your .env:")
    print(f"WHOOP_REFRESH_TOKEN={body['refresh_token']}")
    print(f"\nAccess token (expires in {body.get('expires_in')}s): {body['access_token']}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
