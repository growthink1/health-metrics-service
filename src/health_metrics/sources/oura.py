"""Oura v2 client. Auth: Personal Access Token bearer header."""

from datetime import date, timedelta
from typing import Any

import httpx
import structlog

from .base import OuraDayPayload

log = structlog.get_logger()


class OuraClient:
    def __init__(self, token: str, base_url: str = "https://api.ouraring.com/v2"):
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._http = httpx.AsyncClient(
            timeout=30.0,
            headers={"Authorization": f"Bearer {self._token}"},
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        url = f"{self._base_url}/{path.lstrip('/')}"
        resp = await self._http.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    async def fetch_day(self, day: date) -> OuraDayPayload:
        d = day.isoformat()
        same_day_params = {"start_date": d, "end_date": d}

        daily_sleep = await self._get("usercollection/daily_sleep", same_day_params)
        # The /sleep endpoint filters by bedtime_start (when the session began),
        # not by the wake-up day. A same-day query misses overnight sessions that
        # started the previous evening — which is almost every long_sleep. Widen
        # the window to (d-1, d) and filter by the session's `day` field below.
        sleep_params = {
            "start_date": (day - timedelta(days=1)).isoformat(),
            "end_date": d,
        }
        sleep = await self._get("usercollection/sleep", sleep_params)
        daily_readiness = await self._get(
            "usercollection/daily_readiness", same_day_params
        )
        daily_activity = await self._get(
            "usercollection/daily_activity", same_day_params
        )

        sleep_score = _first(daily_sleep, "score")
        readiness = _first(daily_readiness, "score")
        temp_dev = _first(daily_readiness, "temperature_deviation")

        # Only keep sessions whose `day` matches the target (drops previous-day
        # naps that happened to fall inside the widened window).
        same_day_sessions = [
            s for s in sleep.get("data", []) if s.get("day") == d
        ]
        primary_sleep = _longest_sleep_session(same_day_sessions)
        total_sec = (primary_sleep or {}).get("total_sleep_duration")
        awake_sec = (primary_sleep or {}).get("awake_time")
        rem_sec = (primary_sleep or {}).get("rem_sleep_duration")
        deep_sec = (primary_sleep or {}).get("deep_sleep_duration")
        light_sec = (primary_sleep or {}).get("light_sleep_duration")
        latency_sec = (primary_sleep or {}).get("latency")
        efficiency = (primary_sleep or {}).get("efficiency")
        hrv_avg = (primary_sleep or {}).get("average_hrv")
        rhr = (primary_sleep or {}).get("lowest_heart_rate")

        return OuraDayPayload(
            metric_date=day,
            sleep_score=sleep_score,
            sleep_duration_min=_sec_to_min(total_sec),
            sleep_efficiency=efficiency,
            sleep_latency_min=_sec_to_min(latency_sec),
            rem_min=_sec_to_min(rem_sec),
            deep_min=_sec_to_min(deep_sec),
            light_min=_sec_to_min(light_sec),
            awake_min=_sec_to_min(awake_sec),
            hrv_avg=hrv_avg,
            rhr=rhr,
            temp_deviation=temp_dev,
            readiness_score=readiness,
            raw={
                "daily_sleep": daily_sleep,
                "sleep": sleep,
                "daily_readiness": daily_readiness,
                "daily_activity": daily_activity,
            },
        )


def _first(envelope: dict[str, Any], key: str) -> Any:
    data = envelope.get("data") or []
    if not data:
        return None
    return data[0].get(key)


def _longest_sleep_session(sessions: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not sessions:
        return None
    return max(sessions, key=lambda s: s.get("total_sleep_duration") or 0)


def _sec_to_min(sec: int | None) -> int | None:
    if sec is None:
        return None
    return int(sec) // 60
