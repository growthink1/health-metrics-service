from health_metrics.regulation.strava_notes import parse_strava_note


def test_parse_full_strava_fragment():
    got = parse_strava_note("... Strava: 2.72 mi, 55:34, 118 ft ...")
    assert got == {"distance_mi": 2.72, "duration_min": 55, "elevation_ft": 118}


def test_parse_no_elevation():
    got = parse_strava_note("Strava: 2.6 mi, 58:10")
    assert got["distance_mi"] == 2.6
    assert got["duration_min"] == 58
    assert got["elevation_ft"] is None


def test_parse_no_match_returns_none():
    assert parse_strava_note("[DAY-CLOSE] nothing here") is None
