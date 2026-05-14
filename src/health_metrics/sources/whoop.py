"""Whoop developer v1 client with OAuth refresh-token rotation."""

from datetime import date, datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

import httpx
import structlog

from .base import WhoopDayPayload, WhoopWorkout

log = structlog.get_logger()


# Whoop sport_id → human label (small subset; expand as needed)
SPORT_ID_TO_TYPE: dict[int, str] = {
    -1: "activity",
    0: "running",
    1: "cycling",
    16: "baseball",
    17: "basketball",
    24: "golf",
    33: "hockey",
    42: "lacrosse",
    44: "rugby",
    45: "sailing",
    47: "skiing",
    48: "soccer",
    49: "softball",
    51: "squash",
    52: "swimming",
    53: "tennis",
    55: "volleyball",
    56: "water_polo",
    60: "yoga",
    61: "weightlifting",
    62: "crossfit",
    63: "functional_fitness",
    64: "pilates",
    65: "hiit",
    66: "spin",
    67: "stairs",
    68: "conditioning",
    69: "hiking",
    70: "rowing",
}


TokenRefreshCallback = Callable[[str, str, datetime], Awaitable[None]]


class WhoopClient:
    """
    Async Whoop client.

    On 401, transparently refreshes the access token using the refresh_token
    grant. Whoop rotates refresh tokens on every refresh — the optional
    on_token_refresh callback is invoked with (access_token, refresh_token,
    expires_at) so the caller can persist them.
    """

    def __init__(
        self,
        access_token: str,
        refresh_token: str,
        client_id: str,
        client_secret: str,
        base_url: str = "https://api.prod.whoop.com/developer/v1",
        oauth_url: str = "https://api.prod.whoop.com/oauth/oauth2/token",
        on_token_refresh: TokenRefreshCallback | None = None,
    ):
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._client_id = client_id
        self._client_secret = client_secret
        self._base_url = base_url.rstrip("/")
        self._oauth_url = oauth_url
        self._on_token_refresh = on_token_refresh
        self._http = httpx.AsyncClient(timeout=30.0)

    async def close(self) -> None:
        await self._http.aclose()

    @property
    def access_token(self) -> str:
        return self._access_token

    @property
    def refresh_token(self) -> str:
        return self._refresh_token

    async def _refresh(self) -> None:
        data = {
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "scope": "offline",
        }
        resp = await self._http.post(self._oauth_url, data=data)
        resp.raise_for_status()
        body = resp.json()
        self._access_token = body["access_token"]
        self._refresh_token = body.get("refresh_token", self._refresh_token)
        expires_in = int(body.get("expires_in", 3600))
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        log.info("whoop_token_refreshed", expires_at=expires_at.isoformat())
        if self._on_token_refresh:
            await self._on_token_refresh(self._access_token, self._refresh_token, expires_at)

    async def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        if not self._access_token:
            await self._refresh()
        url = f"{self._base_url}/{path.lstrip('/')}"
        headers = {"Authorization": f"Bearer {self._access_token}"}
        resp = await self._http.get(url, params=params, headers=headers)
        if resp.status_code == 401:
            await self._refresh()
            headers["Authorization"] = f"Bearer {self._access_token}"
            resp = await self._http.get(url, params=params, headers=headers)
        resp.raise_for_status()
        return resp.json()

    async def fetch_day(self, day: date) -> tuple[WhoopDayPayload, list[WhoopWorkout]]:
        start = f"{day.isoformat()}T00:00:00.000Z"
        end = f"{day.isoformat()}T23:59:59.999Z"
        params = {"start": start, "end": end}

        cycle = await self._get("cycle", params)
        recovery = await self._get("recovery", params)
        sleep = await self._get("activity/sleep", params)
        workout = await self._get("activity/workout", params)

        cycle_rec = _first_record(cycle)
        rec_rec = _first_record(recovery)
        sleep_rec = _first_record(sleep)

        cycle_score = (cycle_rec or {}).get("score") or {}
        rec_score = (rec_rec or {}).get("score") or {}
        sleep_score = (sleep_rec or {}).get("score") or {}
        sleep_needed = sleep_score.get("sleep_needed") or {}

        day_payload = WhoopDayPayload(
            metric_date=day,
            recovery_score=rec_score.get("recovery_score"),
            hrv_ms=rec_score.get("hrv_rmssd_milli"),
            rhr=rec_score.get("resting_heart_rate"),
            sleep_performance=sleep_score.get("sleep_performance_percentage"),
            sleep_need_min=_ms_to_min(sleep_needed.get("baseline_milli")),
            sleep_debt_min=_ms_to_min(sleep_needed.get("need_from_sleep_debt_milli")),
            day_strain=cycle_score.get("strain"),
            avg_hr=cycle_score.get("average_heart_rate"),
            max_hr=cycle_score.get("max_heart_rate"),
            kcal_burned=_kj_to_kcal(cycle_score.get("kilojoule")),
            raw={
                "cycle": cycle,
                "recovery": recovery,
                "sleep": sleep,
                "workout": workout,
            },
        )

        workouts: list[WhoopWorkout] = []
        for w in workout.get("records", []):
            workouts.append(_parse_workout(w, day))

        return day_payload, workouts


def _first_record(envelope: dict[str, Any]) -> dict[str, Any] | None:
    recs = envelope.get("records") or []
    if not recs:
        return None
    return recs[0]


def _ms_to_min(ms: int | float | None) -> int | None:
    if ms is None:
        return None
    return int(int(ms) / 60000)


def _kj_to_kcal(kj: float | int | None) -> int | None:
    if kj is None:
        return None
    return int(round(float(kj) * 0.239))


def _parse_workout(w: dict[str, Any], requested_day: date) -> WhoopWorkout:
    score = w.get("score") or {}
    zd = score.get("zone_duration") or {}
    zone_minutes = {
        0: _ms_to_min(zd.get("zone_zero_milli")) or 0,
        1: _ms_to_min(zd.get("zone_one_milli")) or 0,
        2: _ms_to_min(zd.get("zone_two_milli")) or 0,
        3: _ms_to_min(zd.get("zone_three_milli")) or 0,
        4: _ms_to_min(zd.get("zone_four_milli")) or 0,
        5: _ms_to_min(zd.get("zone_five_milli")) or 0,
    }
    start_iso = w["start"]
    end_iso = w["end"]
    start_dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
    end_dt = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
    duration_min = int((end_dt - start_dt).total_seconds() / 60)
    workout_date = start_dt.date()
    sport_id = w.get("sport_id")
    workout_type = SPORT_ID_TO_TYPE.get(sport_id) if sport_id is not None else None
    return WhoopWorkout(
        source_id=str(w["id"]),
        workout_date=workout_date,
        workout_type=workout_type,
        started_at=start_iso,
        duration_min=duration_min,
        avg_hr=score.get("average_heart_rate"),
        max_hr=score.get("max_heart_rate"),
        strain=score.get("strain"),
        kcal=_kj_to_kcal(score.get("kilojoule")),
        zone_minutes=zone_minutes,
        raw=w,
    )
