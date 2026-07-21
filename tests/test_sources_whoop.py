import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from health_metrics.sources.whoop import WhoopClient


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "whoop_responses.json"


@pytest.fixture
def whoop_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text())


@pytest.mark.asyncio
@respx.mock
async def test_whoop_client_fetches_and_normalizes(whoop_fixture):
    day = "2026-05-12"
    fx = whoop_fixture[day]

    respx.get("https://api.prod.whoop.com/developer/v2/cycle").mock(
        return_value=httpx.Response(200, json=fx["cycle"])
    )
    respx.get("https://api.prod.whoop.com/developer/v2/recovery").mock(
        return_value=httpx.Response(200, json=fx["recovery"])
    )
    respx.get("https://api.prod.whoop.com/developer/v2/activity/sleep").mock(
        return_value=httpx.Response(200, json=fx["sleep"])
    )
    respx.get("https://api.prod.whoop.com/developer/v2/activity/workout").mock(
        return_value=httpx.Response(200, json=fx["workout"])
    )

    client = WhoopClient(
        access_token="test-access",
        refresh_token="test-refresh",
        client_id="cid",
        client_secret="csec",
    )
    day_payload, workouts = await client.fetch_day(date.fromisoformat(day))
    await client.close()

    assert day_payload.recovery_score == 65
    assert day_payload.hrv_ms == 42.5
    assert day_payload.rhr == 60
    assert day_payload.sleep_performance == 82
    # 5,400,000 ms / 60000 = 90 min sleep debt
    assert day_payload.sleep_debt_min == 90
    # baseline_milli 28,800,000 / 60000 = 480
    assert day_payload.sleep_need_min == 480
    assert day_payload.day_strain == 14.2
    # 9800.5 kJ * 0.239 = 2342.32, rounded to int = 2342
    assert day_payload.kcal_burned == int(round(9800.5 * 0.239))
    assert day_payload.avg_hr == 92
    assert day_payload.max_hr == 165

    assert len(workouts) == 1
    w = workouts[0]
    assert w.source_id == "wkt-abc123"
    assert w.workout_date == date(2026, 5, 12)
    assert w.workout_type == "cycling"
    assert w.duration_min == 45
    assert w.strain == 14.2
    assert w.avg_hr == 135
    assert w.max_hr == 168
    # zone_zero_milli=60000 → 1 min, zone_two_milli=720000 → 12 min
    assert w.zone_minutes[0] == 1
    assert w.zone_minutes[2] == 12


@pytest.mark.asyncio
@respx.mock
async def test_whoop_client_refreshes_on_401():
    """On 401, client should hit OAuth token endpoint, persist new tokens via callback, retry."""
    refreshed_tokens = {}

    async def on_refresh(access, refresh, expires_at):
        refreshed_tokens["access"] = access
        refreshed_tokens["refresh"] = refresh
        refreshed_tokens["expires_at"] = expires_at

    # First /cycle call → 401, then refresh, then /cycle works.
    cycle_route = respx.get("https://api.prod.whoop.com/developer/v2/cycle")
    cycle_route.side_effect = [
        httpx.Response(401, json={"error": "expired"}),
        httpx.Response(200, json={"records": [], "next_token": None}),
    ]
    respx.get("https://api.prod.whoop.com/developer/v2/recovery").mock(
        return_value=httpx.Response(200, json={"records": [], "next_token": None})
    )
    respx.get("https://api.prod.whoop.com/developer/v2/activity/sleep").mock(
        return_value=httpx.Response(200, json={"records": [], "next_token": None})
    )
    respx.get("https://api.prod.whoop.com/developer/v2/activity/workout").mock(
        return_value=httpx.Response(200, json={"records": [], "next_token": None})
    )
    respx.post("https://api.prod.whoop.com/oauth/oauth2/token").mock(
        return_value=httpx.Response(200, json={
            "access_token": "new-access",
            "refresh_token": "new-refresh",   # rotated!
            "expires_in": 3600,
            "token_type": "Bearer",
        })
    )

    client = WhoopClient(
        access_token="stale-access",
        refresh_token="old-refresh",
        client_id="cid",
        client_secret="csec",
        on_token_refresh=on_refresh,
    )
    await client.fetch_day(date(2026, 5, 12))
    await client.close()

    assert client.access_token == "new-access"
    assert client.refresh_token == "new-refresh"
    assert refreshed_tokens["access"] == "new-access"
    assert refreshed_tokens["refresh"] == "new-refresh"


@pytest.mark.asyncio
@respx.mock
async def test_whoop_partial_failure_recovers_remaining_endpoints():
    """If one endpoint 404s, the others still produce data."""
    respx.get("https://api.prod.whoop.com/developer/v2/cycle").mock(
        return_value=httpx.Response(200, json={"records": [{
            "id": 999, "user_id": 1, "start": "2026-05-13T07:00:00.000Z",
            "end": "2026-05-14T07:00:00.000Z", "timezone_offset": "-04:00",
            "score_state": "SCORED",
            "score": {"strain": 11.5, "kilojoule": 8000.0, "average_heart_rate": 88, "max_heart_rate": 150},
        }], "next_token": None})
    )
    respx.get("https://api.prod.whoop.com/developer/v2/recovery").mock(
        return_value=httpx.Response(404, text="HTTP 404 Not Found")
    )
    respx.get("https://api.prod.whoop.com/developer/v2/activity/sleep").mock(
        return_value=httpx.Response(200, json={"records": [], "next_token": None})
    )
    respx.get("https://api.prod.whoop.com/developer/v2/activity/workout").mock(
        return_value=httpx.Response(200, json={"records": [], "next_token": None})
    )

    client = WhoopClient(
        access_token="test-access",
        refresh_token="test-refresh",
        client_id="cid",
        client_secret="csec",
    )
    day_payload, workouts = await client.fetch_day(date(2026, 5, 13))
    await client.close()

    # Cycle worked → day_strain populated
    assert day_payload.day_strain == 11.5
    assert day_payload.avg_hr == 88
    # Recovery 404'd → recovery_score is None
    assert day_payload.recovery_score is None
    assert day_payload.hrv_ms is None
    # Sleep/workout empty (not failed) → all None
    assert day_payload.sleep_performance is None
    assert len(workouts) == 0


@respx.mock
async def test_whoop_refresh_failure_raises_auth_error():
    """A failed token refresh (invalid_grant) must raise WhoopAuthError rather than
    being swallowed as empty data — so the ingest can mark whoop_status='auth_error'
    instead of a misleading 'ok'."""
    from health_metrics.sources.whoop import WhoopAuthError

    # Empty access token forces a refresh on the first call; the token endpoint
    # rejects the (dead) refresh token.
    respx.post("https://api.prod.whoop.com/oauth/oauth2/token").mock(
        return_value=httpx.Response(400, json={"error": "invalid_grant"})
    )
    client = WhoopClient(
        access_token="",
        refresh_token="dead-refresh",
        client_id="cid",
        client_secret="csec",
    )
    with pytest.raises(WhoopAuthError):
        await client.fetch_day(date(2026, 7, 20))
    await client.close()
