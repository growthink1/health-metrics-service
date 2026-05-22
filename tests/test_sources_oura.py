import json
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx

from health_metrics.sources.oura import OuraClient


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "oura_responses.json"


@pytest.fixture
def oura_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text())


@pytest.mark.asyncio
@respx.mock
async def test_oura_sleep_widens_window_and_filters_by_day():
    """The Oura /sleep endpoint's start_date/end_date filter on the session's
    bedtime calendar date (NOT the `day` wake-up field). A same-day query
    misses every overnight session (bedtime is on day-1). Fix: widen window
    to (d, d+1) and filter the returned sessions by `day` field."""
    target = date(2026, 5, 12)

    # daily_sleep + readiness + activity respond with empty data (irrelevant to this test)
    respx.get("https://api.ouraring.com/v2/usercollection/daily_sleep").mock(
        return_value=httpx.Response(200, json={"data": [], "next_token": None})
    )
    respx.get("https://api.ouraring.com/v2/usercollection/daily_readiness").mock(
        return_value=httpx.Response(200, json={"data": [], "next_token": None})
    )
    respx.get("https://api.ouraring.com/v2/usercollection/daily_activity").mock(
        return_value=httpx.Response(200, json={"data": [], "next_token": None})
    )

    # /sleep — two sessions in the returned (d, d+1) window:
    #   - target-day overnight (day=2026-05-12, bedtime_start=2026-05-11T23:30) — picked
    #   - next-day nap (day=2026-05-13) — ignored
    sleep_route = respx.get(
        "https://api.ouraring.com/v2/usercollection/sleep",
        params={"start_date": "2026-05-12", "end_date": "2026-05-13"},
    ).mock(return_value=httpx.Response(200, json={
        "data": [
            {  # target-day overnight — must be selected
                "id": "long-1", "day": "2026-05-12", "type": "long_sleep",
                "bedtime_start": "2026-05-11T23:30:00-04:00",
                "total_sleep_duration": 24720, "average_hrv": 45, "lowest_heart_rate": 58,
                "rem_sleep_duration": 5040, "deep_sleep_duration": 4200, "light_sleep_duration": 15480,
                "awake_time": 1080, "latency": 540, "efficiency": 89.2,
            },
            {  # next-day nap — must NOT be selected
                "id": "nap-1", "day": "2026-05-13", "type": "rest",
                "bedtime_start": "2026-05-13T14:00:00-04:00",
                "total_sleep_duration": 1800, "average_hrv": 30, "lowest_heart_rate": 70,
                "rem_sleep_duration": 0, "deep_sleep_duration": 0, "light_sleep_duration": 1800,
                "awake_time": 0, "latency": 0, "efficiency": 100.0,
            },
        ],
        "next_token": None,
    }))

    client = OuraClient(token="test-token")
    payload = await client.fetch_day(target)
    await client.close()

    # The route was called with the widened window (assertion #1).
    assert sleep_route.called, "sleep route must be called with (d-1, d) widened window"

    # The parser picked the target-day session (assertion #2), NOT the nap.
    assert payload.sleep_duration_min == 24720 // 60
    assert payload.hrv_avg == 45
    assert payload.rhr == 58
    assert payload.sleep_efficiency == 89.2


@pytest.mark.asyncio
@respx.mock
async def test_oura_sleep_returns_none_when_no_matching_day_session():
    """If /sleep returns sessions but none have `day == target`, sleep_duration_min
    must be None (not the first session's data, not zero)."""
    target = date(2026, 5, 12)

    respx.get("https://api.ouraring.com/v2/usercollection/daily_sleep").mock(
        return_value=httpx.Response(200, json={"data": [], "next_token": None})
    )
    respx.get("https://api.ouraring.com/v2/usercollection/daily_readiness").mock(
        return_value=httpx.Response(200, json={"data": [], "next_token": None})
    )
    respx.get("https://api.ouraring.com/v2/usercollection/daily_activity").mock(
        return_value=httpx.Response(200, json={"data": [], "next_token": None})
    )
    respx.get("https://api.ouraring.com/v2/usercollection/sleep").mock(
        return_value=httpx.Response(200, json={"data": [
            {  # next-day nap leaks into (d, d+1) window — must be filtered out
                "id": "nap-1", "day": "2026-05-13", "type": "rest",
                "bedtime_start": "2026-05-13T14:00:00-04:00",
                "total_sleep_duration": 1800, "average_hrv": 30, "lowest_heart_rate": 70,
            },
        ], "next_token": None})
    )

    client = OuraClient(token="test-token")
    payload = await client.fetch_day(target)
    await client.close()

    assert payload.sleep_duration_min is None
    assert payload.hrv_avg is None
    assert payload.rhr is None


@pytest.mark.asyncio
@respx.mock
async def test_oura_client_fetches_and_normalizes_single_date(oura_fixture):
    day = "2026-05-12"
    fx = oura_fixture[day]

    respx.get("https://api.ouraring.com/v2/usercollection/daily_sleep").mock(
        return_value=httpx.Response(200, json=fx["daily_sleep"])
    )
    respx.get("https://api.ouraring.com/v2/usercollection/sleep").mock(
        return_value=httpx.Response(200, json=fx["sleep"])
    )
    respx.get("https://api.ouraring.com/v2/usercollection/daily_readiness").mock(
        return_value=httpx.Response(200, json=fx["daily_readiness"])
    )
    respx.get("https://api.ouraring.com/v2/usercollection/daily_activity").mock(
        return_value=httpx.Response(200, json=fx["daily_activity"])
    )

    client = OuraClient(token="test-token")
    payload = await client.fetch_day(date.fromisoformat(day))
    await client.close()

    assert payload.metric_date == date(2026, 5, 12)
    assert payload.sleep_score == 78
    assert payload.sleep_duration_min == 24720 // 60          # = 412
    assert payload.sleep_efficiency == 89.2
    assert payload.sleep_latency_min == 540 // 60              # = 9
    assert payload.rem_min == 5040 // 60                       # = 84
    assert payload.deep_min == 4200 // 60                      # = 70
    assert payload.light_min == 15480 // 60                    # = 258
    assert payload.awake_min == 1080 // 60                     # = 18
    assert payload.hrv_avg == 45
    assert payload.rhr == 58
    assert payload.temp_deviation == 0.3
    assert payload.readiness_score == 72
