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
