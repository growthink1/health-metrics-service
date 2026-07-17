"""Best-effort parser for 'Strava: X mi, MM:SS, Y ft' fragments in manual_log.notes.

Conservative: returns None unless it finds a clear `Strava: <dist> mi, <mm:ss>`
pattern. Used for the Item #6 backfill of Whoop-missed walk days.
"""

from __future__ import annotations

import re

_RE = re.compile(
    r"Strava:\s*([\d.]+)\s*mi\s*,\s*(\d{1,2}):(\d{2})(?:\s*,\s*([\d,]+)\s*ft)?",
    re.IGNORECASE,
)


def parse_strava_note(note: str) -> dict | None:
    m = _RE.search(note or "")
    if not m:
        return None
    dist = float(m.group(1))
    minutes = int(m.group(2))
    elev = int(m.group(4).replace(",", "")) if m.group(4) else None
    return {"distance_mi": dist, "duration_min": minutes, "elevation_ft": elev}
