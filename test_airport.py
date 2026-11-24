"""Tests for the transformations, metric rules, offline data and consumer separation.

    pytest -v
"""
from datetime import date, datetime, timedelta

import pytest

import config

from airport_transform import (IST, ValidationError, active_end, busy_level, capacity_factor,
                               ceiling_ft, delay_bucket, delay_minutes, flight_category,
                               is_on_time, parse_time, relative_humidity, runway_for,
                               transform_flight, transform_metar, validate_flight, validate_metar,
                               visibility_km, wind_components)
from consumer_ops import ops_flight_view
from consumer_public import advisory, public_flight_view, public_weather_view
from offline_data import flight_state, hash_str, make_rng, metar_at, schedule_for

DAY = date(2026, 11, 24)


def flight_event(**changes):
    f = {"flight_id": "20261124-6E123-DEP", "flight_iata": "6E123", "airline_iata": "6E",
         "airline_name": "IndiGo", "movement": "DEP", "origin_iata": "BLR", "origin_city": "Bengaluru",
         "dest_iata": "DEL", "dest_city": "Delhi", "aircraft": "A320neo", "terminal": "T1",
         "gate": "112", "runway": "09R", "status": "Delayed",
         "sched_time": "2026-11-24T10:00:00+05:30", "est_time": "2026-11-24T10:40:00+05:30",
         "actual_time": None, "runway_time": None}
    f.update(changes)
    return {"event_id": "x", "emitted_at": "2026-11-24T04:00:00+00:00", "source": "offline", "flight": f}


def metar_event(**changes):
    m = {"icaoId": "VOBL", "reportTime": "2026-11-24T01:00:00Z", "temp": 18, "dewp": 16, "wdir": 90,
         "wspd": 8, "wgst": None, "visib": "6+", "altim": 1016, "wxString": None,
         "clouds": [{"cover": "SCT", "base": 2000}], "rawOb": "METAR VOBL 240100Z 09008KT 9999 SCT020 18/16 Q1016"}
    m.update(changes)
    return {"event_id": "y", "emitted_at": "2026-11-24T01:05:00+00:00", "source": "offline", "metar": m}


# ---------------------------------------------------------------------------
# Flight transformation and metric rules
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("delay, on_time", [(14, True), (15, False), (16, False), (-5, True)])
def test_on_time_is_less_than_15_minutes_late(delay, on_time):
    assert is_on_time(delay) is on_time


def test_delay_bands():
    assert delay_bucket(14, "Departed") == "On time (<15 min)"
    assert delay_bucket(15, "Departed") == "15-29 min late"
    assert delay_bucket(59, "Departed") == "30-59 min late"
    assert delay_bucket(60, "Departed") == "60+ min late"
    assert delay_bucket(0, "Cancelled") == "Cancelled"


def test_times_are_converted_to_ist_and_delay_uses_actual_first():
    t = parse_time("2026-11-24T04:30:00Z")
    assert t.utcoffset() == timedelta(hours=5, minutes=30) and t.hour == 10
    assert parse_time("2026-11-24T10:00:00").tzinfo is not None          # no offset -> airport time
    s, e, a = parse_time("2026-11-24T10:00"), parse_time("2026-11-24T10:20"), parse_time("2026-11-24T10:35")
    assert delay_minutes(s, e) == 20 and delay_minutes(s, e, a) == 35


def test_transform_flight():
    m = transform_flight(flight_event())
    assert m["delay_min"] == 40 and not m["on_time"] and m["bucket"] == "30-59 min late"
    assert not m["completed"]
    m = transform_flight(flight_event(status="Departed", actual_time="2026-11-24T10:05:00+05:30",
                                      runway_time="2026-11-24T10:18:00+05:30"))
    assert m["delay_min"] == 5 and m["on_time"] and m["completed"]


def test_flight_validation_rejects_bad_events():
    with pytest.raises(ValidationError, match="missing"):
        validate_flight(flight_event(flight_iata=""))
    with pytest.raises(ValidationError, match="ARR or DEP"):
        validate_flight(flight_event(movement="XYZ"))
    with pytest.raises(ValidationError, match="unknown DEP status"):
        validate_flight(flight_event(status="Landed"))
    with pytest.raises(ValidationError, match="not a valid time"):
        validate_flight(flight_event(sched_time="tomorrow"))
    with pytest.raises(ValidationError):
        validate_flight({})


# ---------------------------------------------------------------------------
# Weather transformation
# ---------------------------------------------------------------------------
def test_units_visibility_and_ceiling():
    assert visibility_km("6+") == 10.0
    assert visibility_km(3.11) == 5.0                       # statute miles -> km
    assert ceiling_ft([{"cover": "SCT", "base": 800}, {"cover": "BKN", "base": 1500}]) == 1500
    assert ceiling_ft([{"cover": "FEW", "base": 2500}]) is None


@pytest.mark.parametrize("vis_km, ceil, cat", [
    (10, None, "VFR"), (10, 3000, "MVFR"), (8.0, None, "MVFR"), (4.0, None, "IFR"),
    (10, 900, "IFR"), (1.5, None, "LIFR"), (10, 400, "LIFR")])
def test_flight_category(vis_km, ceil, cat):
    assert flight_category(vis_km, ceil) == cat


def test_wind_components_and_runway_in_use():
    assert wind_components(90, 10, 90) == (10.0, 0.0)         # straight down the runway
    assert wind_components(180, 10, 90) == (0.0, 10.0)        # straight across
    assert wind_components(270, 10, 90) == (-10.0, 0.0)       # tailwind
    assert active_end(90, 10) == "09" and active_end(260, 10) == "27"
    assert active_end(260, 2) == "09"                         # calm -> preferred end
    assert runway_for("ARR", "27") == "27R" and runway_for("DEP", "09") == "09R"


def test_capacity_and_busy_levels():
    assert capacity_factor("VFR", "") == 1.0
    assert capacity_factor("IFR", "TSRA") == pytest.approx(0.52)
    assert busy_level(49.9) == "Low" and busy_level(50) == "Moderate"
    assert busy_level(75) == "High" and busy_level(90) == "Saturated"


def test_transform_metar():
    w = transform_metar(metar_event())
    assert w["category"] == "VFR" and w["active_end"] == "09" and w["capacity_per_runway"] == 30
    assert w["wind_kmh"] == pytest.approx(14.8) and w["rh"] == relative_humidity(18, 16) == 88
    fog = transform_metar(metar_event(visib=0.5, wxString="FG", clouds=[{"cover": "BKN", "base": 300}]))
    assert fog["category"] == "LIFR" and fog["capacity_per_runway"] == 18 and fog["condition"] == "Fog"


def test_metar_validation():
    with pytest.raises(ValidationError, match="missing temp"):
        validate_metar(metar_event(temp=None))
    with pytest.raises(ValidationError, match="dew point"):
        validate_metar(metar_event(dewp=25))
    with pytest.raises(ValidationError, match="QNH"):
        validate_metar(metar_event(altim=29.92))


# ---------------------------------------------------------------------------
# Offline data
# ---------------------------------------------------------------------------
def test_offline_data_is_repeatable_and_valid():
    assert schedule_for(DAY) == schedule_for(DAY)
    t = datetime(2026, 11, 24, 6, 30, tzinfo=IST)
    assert metar_at(t) == metar_at(t + timedelta(minutes=10))     # same half hour
    validate_metar({"metar": metar_at(t)})
    assert 600 < len(schedule_for(DAY)) < 720


def test_random_generator_reference_values():
    assert hash_str("Mandya2026-11-4") == 894695567
    r = make_rng(hash_str("Mandya2026-11-4"))
    assert [round(r(), 10) for _ in range(3)] == [0.8477702253, 0.0437314045, 0.329369053]


def test_departure_status_timeline():
    f = next(x for x in schedule_for(DAY) if x["movement"] == "DEP" and not x["cancelled"])
    est = f["sched"] + timedelta(minutes=f["delay"])
    assert flight_state(f, f["sched"] - timedelta(hours=5))["status"] == "Scheduled"
    assert flight_state(f, est - timedelta(minutes=30))["status"] == "Boarding"
    assert flight_state(f, est - timedelta(minutes=5))["status"] == "Gate Closed"
    done = flight_state(f, est + timedelta(minutes=f["taxi"] + 1))
    assert done["status"] == "Departed" and done["runway_time"] > done["actual_time"]
    validate_flight({"flight": {**{k: f[k] for k in ("flight_id", "flight_iata", "airline_iata",
                     "airline_name", "movement", "origin_iata", "dest_iata")},
                     "status": done["status"], "sched_time": f["sched"].isoformat()}})


# ---------------------------------------------------------------------------
# The two consumers keep different fields
# ---------------------------------------------------------------------------
def test_consumers_keep_separate_fields():
    m = transform_flight(flight_event())
    pub, ops = public_flight_view(m), ops_flight_view(m)
    assert "runway" not in pub and "aircraft" not in pub and pub["city"] == "Delhi"
    assert ops["runway"] == "09R" and ops["aircraft"] == "A320neo" and "city" not in ops
    live = transform_flight(flight_event(runway=None))
    assert ops_flight_view(live, "27")["runway"] == "27L"         # assigned from the wind


def test_public_weather_and_advisory():
    w = transform_metar(metar_event())
    v = public_weather_view(w)
    assert "category" not in v and "crosswind_kt" not in v and v["wind_dir"] == "E"
    assert advisory(w) == "No weather disruption expected"
    storm = transform_metar(metar_event(wxString="TSRA", wgst=34))
    assert advisory(storm).startswith("Thunderstorm")
    low = transform_metar(metar_event(visib=1.2))
    assert advisory(low).startswith("Low visibility")


# ---------------------------------------------------------------------------
# Live positions and the web dashboard
# ---------------------------------------------------------------------------
def test_distance_bearing_and_callsigns():
    from airport_transform import bearing_deg, distance_km, flight_from_callsign
    assert distance_km(13.1986, 77.7066, 12.9716, 77.5946) == pytest.approx(28.0, abs=0.2)  # BLR -> city centre
    assert bearing_deg(13.0, 77.0, 14.0, 77.0) == pytest.approx(0, abs=0.01)                # due north
    assert bearing_deg(13.0, 77.0, 13.0, 78.0) == pytest.approx(90, abs=0.2)                # due east
    assert flight_from_callsign("IGO5614 ") == "6E5614" and flight_from_callsign("UAE566") == "EK566"
    assert flight_from_callsign("VTABC") == "VTABC"


def test_flight_phase():
    from airport_transform import flight_phase
    assert flight_phase(4000, -700, 90, 10, 270) == "Arriving"       # west of BLR, heading east, descending
    assert flight_phase(8000, 2000, 95, 12, 90) == "Departing"       # east of BLR, heading away, climbing
    assert flight_phase(35000, 0, 30, 40, 200) == "Overflying"
    assert flight_phase(6200, -800, 202, 14, 281) == "Arriving"      # turning onto final, not yet facing BLR
    assert flight_phase(0, 0, 90, 1, 90, on_ground=True) == "On ground"


def test_positions_validation_and_radius():
    from airport_transform import transform_positions, validate_positions
    snap = {"snapshot_time": "2026-11-24T12:00:00Z", "aircraft": [
        {"hex": "800ABC", "flight": "IGO5614", "lat": 13.20, "lon": 77.55, "alt_baro": 5000, "gs": 160,
         "track": 88, "baro_rate": -700},
        {"hex": "800DEF", "flight": "AIC804", "lat": 15.00, "lon": 77.70, "alt_baro": 30000}]}   # 200 km away
    validate_positions(snap)
    m = transform_positions(snap)
    assert [a["hex"] for a in m["aircraft"]] == ["800abc"]
    assert m["aircraft"][0]["phase"] == "Arriving" and m["aircraft"][0]["flight_iata"] == "6E5614"
    with pytest.raises(ValidationError, match="bad position"):
        validate_positions({"snapshot_time": "x", "aircraft": [{"hex": "1", "lat": 123, "lon": 0}]})
    with pytest.raises(ValidationError, match="no aircraft list"):
        validate_positions({"snapshot_time": "x"})


def test_offline_positions_follow_the_schedule():
    from airport_transform import transform_positions, validate_positions
    from offline_data import positions_at
    now = datetime(2026, 11, 24, 19, 0, tzinfo=IST)
    snap = {"snapshot_time": now.isoformat(), "aircraft": positions_at(now)}
    validate_positions(snap)
    phases = {a["phase"] for a in transform_positions(snap)["aircraft"]}
    assert len(snap["aircraft"]) >= 4 and {"Arriving", "Departing"} <= phases
    assert positions_at(now) == positions_at(now)
    # Aircraft on final approach line up with the arrival runway (09L/27R), not the airport reference point.
    arr_lat = sum(p[0] for p in config.RUNWAYS[0]["ends"].values()) / 2
    finals = [a for a in transform_positions(snap)["aircraft"] if a["phase"] == "Arriving" and a["dist_km"] < 10]
    assert all(abs(a["lat"] - arr_lat) < 0.01 for a in finals)


def test_web_pages_render():
    from web_dashboard import app
    client = app.test_client()
    for url, text in (("/", b"Arrivals"), ("/ops", b"Runway and airport operations"),
                      ("/airlines", b"Airlines and weather"), ("/stream", b"Stream and health"),
                      ("/map", b"Aircraft around the airport")):
        res = client.get(url)
        assert res.status_code == 200 and text in res.data
    home = client.get("/").get_data(as_text=True)
    ops = client.get("/ops").get_data(as_text=True)
    assert home.count('target="_blank"') >= 2
    assert 'href="/airlines"' in home and 'href="/stream"' in home
    assert "panel-trends" not in ops and "panel-stream" not in ops
    assert 'role="tab"' not in ops and 'id="panel-schedule" hidden' not in ops
    assert 'class="deck vertical"' in home and 'class="deck vertical"' in ops
    assert "data-lat=" in client.get("/map").get_data(as_text=True)


# ---------------------------------------------------------------------------
# Flink job (runs only where PyFlink and Java are installed)
# ---------------------------------------------------------------------------
def test_flink_job_counts_each_movement_once(tmp_path):
    pytest.importorskip("pyflink")
    import json
    import re
    import subprocess
    import sys

    def event(fid, movement, status, delay, runway_time):
        sched = "2026-11-24T10:00:00+05:30"
        late = f"2026-11-24T10:{delay:02d}:00+05:30"
        return {"event_id": f"{fid}-{status}", "emitted_at": "x", "source": "offline", "flight": {
            "flight_id": fid, "flight_iata": fid, "movement": movement, "runway": None, "status": status,
            "sched_time": sched, "est_time": late, "actual_time": late, "runway_time": runway_time}}

    events = [
        event("A1", "ARR", "Landed", 5, "2026-11-24T10:01:00+05:30"),
        event("A1", "ARR", "Arrived", 5, "2026-11-24T10:01:00+05:30"),   # same landing again
        event("D1", "DEP", "Departed", 20, "2026-11-24T10:32:00+05:30"),  # 20 min late
        event("D2", "DEP", "Departed", 3, "2026-11-24T10:14:00+05:30"),
        event("D3", "DEP", "Boarding", 0, None),                          # not on the runway yet
    ]
    src = tmp_path / "events.jsonl"
    src.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    from pathlib import Path

    job = Path(__file__).with_name("flink_runway_job.py")
    try:
        out = subprocess.run([sys.executable, str(job), "--source-file", str(src), "--print"],
                             capture_output=True, text=True, timeout=300, check=True).stdout
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        pytest.skip(f"Flink job unavailable: {exc}")

    final = {}
    for table, op, body in re.findall(r"(flink_\w+)> ([+-][IUD])\[(.*)\]", out):
        vals = [v.strip() for v in body.split(",")]
        if op in ("+I", "+U"):
            final[(table, vals[0], vals[1])] = vals
        elif op == "-D":
            final.pop((table, vals[0], vals[1]), None)
    hourly = {k[2]: v for k, v in final.items() if k[0] == "flink_hourly"}
    assert hourly["ARR"][2] == "1"                     # landed + arrived = one movement
    assert hourly["DEP"][2] == "2" and hourly["DEP"][4] == "1"   # two departures, one late
    buckets = {(k[1], k[2]): v[2] for k, v in final.items() if k[0] == "flink_runway_5min"}
    assert buckets == {("09L/27R", "2026-11-24T10:00"): "1", ("09R/27L", "2026-11-24T10:30"): "1",
                       ("09R/27L", "2026-11-24T10:10"): "1"}
