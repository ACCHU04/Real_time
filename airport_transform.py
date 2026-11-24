"""Validation and transformation shared by both consumers.

Flights: parse times to IST, work out the delay and whether the flight was on time.
Weather: convert METAR units (knots, statute miles, feet), work out the flight
category, the wind along and across each runway, the runway end in use and the
capacity that the weather allows.
"""
import math
from datetime import datetime
from zoneinfo import ZoneInfo

import config

IST = ZoneInfo("Asia/Kolkata")

FLIGHT_FIELDS = ["flight_id", "flight_iata", "airline_iata", "airline_name", "movement",
                 "origin_iata", "dest_iata", "status", "sched_time"]
STATUSES = {
    "DEP": {"Scheduled", "Delayed", "Boarding", "Gate Closed", "Taxiing", "Departed", "Cancelled"},
    "ARR": {"Scheduled", "Delayed", "En Route", "Approaching", "Landed", "Arrived",
            "Cancelled", "Diverted"},
}
DONE = {"DEP": {"Departed"}, "ARR": {"Landed", "Arrived"}}   # the flight has used the runway

KT_TO_KMH = 1.852
SM_TO_KM = 1.609344


class ValidationError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Flights
# ---------------------------------------------------------------------------
def parse_time(value):
    """ISO 8601 string -> aware datetime in IST. None stays None."""
    if value in (None, ""):
        return None
    t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if t.tzinfo is None:
        t = t.replace(tzinfo=IST)            # times without an offset are airport local time
    return t.astimezone(IST)


def validate_flight(event):
    if not isinstance(event, dict) or not isinstance(event.get("flight"), dict):
        raise ValidationError("message has no flight object")
    f = event["flight"]
    missing = [k for k in FLIGHT_FIELDS if f.get(k) in (None, "")]
    if missing:
        raise ValidationError(f"missing {', '.join(missing)}")
    if f["movement"] not in STATUSES:
        raise ValidationError(f"movement must be ARR or DEP, got {f['movement']!r}")
    if f["status"] not in STATUSES[f["movement"]]:
        raise ValidationError(f"unknown {f['movement']} status {f['status']!r}")
    for k in ("sched_time", "est_time", "actual_time", "runway_time"):
        try:
            parse_time(f.get(k))
        except ValueError:
            raise ValidationError(f"{k} is not a valid time: {f.get(k)!r}") from None


def delay_minutes(sched, est=None, actual=None):
    """Minutes late against the schedule (negative = early). Uses actual, else estimate."""
    best = actual or est
    if best is None:
        return 0
    return round((best - sched).total_seconds() / 60)


def is_on_time(delay_min):
    return delay_min < config.ON_TIME_MINUTES


def delay_bucket(delay_min, status):
    if status == "Cancelled":
        return "Cancelled"
    if delay_min < 15:
        return "On time (<15 min)"
    if delay_min < 30:
        return "15-29 min late"
    if delay_min < 60:
        return "30-59 min late"
    return "60+ min late"


def transform_flight(event):
    """Common flight model used by both consumers."""
    f = event["flight"]
    sched, est = parse_time(f["sched_time"]), parse_time(f.get("est_time"))
    actual, runway_time = parse_time(f.get("actual_time")), parse_time(f.get("runway_time"))
    delay = 0 if f["status"] == "Cancelled" else delay_minutes(sched, est, actual)
    return {
        **{k: f.get(k) for k in ("flight_id", "flight_iata", "airline_iata", "airline_name",
                                 "movement", "origin_iata", "origin_city", "dest_iata",
                                 "dest_city", "aircraft", "terminal", "gate", "runway", "status")},
        "sched": sched, "est": est or sched, "actual": actual, "runway_time": runway_time,
        "delay_min": delay,
        "on_time": is_on_time(delay) and f["status"] != "Cancelled",
        "bucket": delay_bucket(delay, f["status"]),
        "completed": f["status"] in DONE[f["movement"]],
    }


# ---------------------------------------------------------------------------
# Weather (METAR)
# ---------------------------------------------------------------------------
def validate_metar(event):
    if not isinstance(event, dict) or not isinstance(event.get("metar"), dict):
        raise ValidationError("message has no metar object")
    m = event["metar"]
    for k in ("icaoId", "reportTime", "temp", "dewp", "wspd", "visib", "altim"):
        if m.get(k) is None:
            raise ValidationError(f"metar missing {k}")
    if not -30 <= float(m["temp"]) <= 55:
        raise ValidationError(f"temperature out of range: {m['temp']}")
    if float(m["dewp"]) > float(m["temp"]) + 0.5:
        raise ValidationError("dew point above temperature")
    if not 900 <= float(m["altim"]) <= 1100:
        raise ValidationError(f"QNH out of range: {m['altim']}")


def visibility_km(visib):
    """METAR API visibility is in statute miles; '6+' means 6 SM or more (shown as 10 km)."""
    if isinstance(visib, str) and visib.endswith("+"):
        return 10.0
    return round(float(visib) * SM_TO_KM, 1)


def ceiling_ft(clouds):
    """Lowest broken or overcast layer, in feet. None means no ceiling."""
    bases = [c["base"] for c in clouds or [] if c.get("cover") in ("BKN", "OVC", "VV") and c.get("base") is not None]
    return min(bases) if bases else None


def flight_category(vis_km, ceiling):
    """FAA flight categories: VFR, MVFR, IFR, LIFR (the worse of visibility and ceiling)."""
    vis_sm = vis_km / SM_TO_KM
    ceil = ceiling if ceiling is not None else 99999
    if vis_sm < 1 or ceil < 500:
        return "LIFR"
    if vis_sm < 3 or ceil < 1000:
        return "IFR"
    if vis_sm <= 5 or ceil <= 3000:
        return "MVFR"
    return "VFR"


def wind_components(wind_dir, wind_kt, runway_heading):
    """Headwind (+) / tailwind (-) and crosswind (always +) for one runway direction."""
    if wind_dir is None or wind_kt == 0:
        return 0.0, 0.0
    angle = math.radians(wind_dir - runway_heading)
    head = round(wind_kt * math.cos(angle), 1)
    cross = round(abs(wind_kt * math.sin(angle)), 1)
    return head + 0.0, cross + 0.0


def active_end(wind_dir, wind_kt, heading=None):
    """Land and take off into the wind: '09' or '27'. Calm or variable -> preferred end."""
    heading = config.RUNWAYS[0]["heading"] if heading is None else heading
    if wind_dir is None or wind_kt < 3:
        return config.PREFERRED_END
    head_09, _ = wind_components(wind_dir, wind_kt, heading)
    if head_09 == 0:
        return config.PREFERRED_END
    return "09" if head_09 > 0 else "27"


def runway_for(movement, end):
    """Runway designator such as '27R' for an arrival when the 27 end is in use."""
    rwy = next(r for r in config.RUNWAYS if r["use"] == movement)["name"]
    e09, e27 = rwy.split("/")
    return e09 if end == "09" else e27


def capacity_factor(category, wx):
    f = config.CAPACITY_FACTOR[category]
    if wx and "TS" in wx:
        f *= config.THUNDERSTORM_FACTOR
    return round(f, 2)


def busy_level(utilisation_pct):
    return next(label for limit, label in config.BUSY_LEVELS if utilisation_pct >= limit)


def relative_humidity(temp, dew):
    """Magnus formula."""
    a, b = 17.625, 243.04
    return round(100 * math.exp(a * dew / (b + dew)) / math.exp(a * temp / (b + temp)))


def feels_like(temp, rh, wind_kmh):
    """Apparent temperature (Australian BoM formula, wind in m/s)."""
    e = rh / 100 * 6.105 * math.exp(17.27 * temp / (237.7 + temp))
    return round(temp + 0.33 * e - 0.70 * (wind_kmh / 3.6) - 4.0, 1)


def compass(deg):
    if deg is None:
        return "Variable"
    names = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return names[round(deg / 45) % 8]


WX_TEXT = [("TS", "Thunderstorm"), ("+RA", "Heavy rain"), ("-RA", "Light rain"), ("RA", "Rain"),
           ("DZ", "Drizzle"), ("FG", "Fog"), ("BR", "Mist"), ("HZ", "Haze")]


def condition(wx, clouds):
    for code, text in WX_TEXT:
        if wx and code in wx:
            return text
    covers = {c.get("cover") for c in clouds or []}
    if "OVC" in covers:
        return "Overcast"
    if "BKN" in covers:
        return "Mostly cloudy"
    if covers & {"SCT", "FEW"}:
        return "Partly cloudy"
    return "Clear"


def transform_metar(event):
    """Common weather model used by both consumers."""
    m = event["metar"]
    wind_dir = None if m.get("wdir") in (None, "VRB") else int(m["wdir"])
    wind_kt, gust_kt = int(m["wspd"]), m.get("wgst")
    vis = visibility_km(m["visib"])
    ceil = ceiling_ft(m.get("clouds"))
    cat = flight_category(vis, ceil)
    end = active_end(wind_dir, wind_kt)
    heading = config.RUNWAYS[0]["heading"] + (0 if end == "09" else 180)
    head, cross = wind_components(wind_dir, max(wind_kt, gust_kt or 0), heading)
    factor = capacity_factor(cat, m.get("wxString"))
    temp, dew = float(m["temp"]), float(m["dewp"])
    rh = relative_humidity(temp, dew)
    return {
        "obs_time": parse_time(m["reportTime"]),
        "raw": m.get("rawOb", ""),
        "temp": temp, "dew": dew, "rh": rh,
        "feels": feels_like(temp, rh, wind_kt * KT_TO_KMH),
        "wind_dir": wind_dir, "wind_kt": wind_kt, "gust_kt": gust_kt,
        "wind_kmh": round(wind_kt * KT_TO_KMH, 1),
        "vis_km": vis, "ceiling_ft": ceil, "qnh": int(m["altim"]),
        "wx": m.get("wxString") or "",
        "condition": condition(m.get("wxString"), m.get("clouds")),
        "category": cat, "active_end": end,
        "headwind_kt": head, "crosswind_kt": cross,
        "crosswind_alert": cross > config.CROSSWIND_LIMIT_KT,
        "capacity_factor": factor,
        "capacity_per_runway": round(config.RUNWAY_CAPACITY_PER_HOUR * factor),
    }


# ---------------------------------------------------------------------------
# Live aircraft positions (ADS-B)
# ---------------------------------------------------------------------------
# ICAO airline code in a call sign -> IATA code on the flight board (IGO5614 -> 6E5614)
ICAO_TO_IATA = {"IGO": "6E", "AIC": "AI", "AXB": "IX", "AKJ": "QP", "SEJ": "SG", "LLR": "9I",
                "SDG": "S5", "UAE": "EK", "QTR": "QR", "SIA": "SQ", "ETD": "EY", "ABY": "G9",
                "THA": "TG", "MAS": "MH", "DLH": "LH", "BAW": "BA"}
IATA_TO_ICAO = {v: k for k, v in ICAO_TO_IATA.items()}
EARTH_KM = 6371.0


def distance_km(lat1, lon1, lat2, lon2):
    """Great-circle distance (haversine)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_KM * math.asin(math.sqrt(a))


def bearing_deg(lat1, lon1, lat2, lon2):
    """Initial bearing from point 1 to point 2, 0-360 degrees (0 = north)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def flight_from_callsign(callsign):
    """'IGO5614' -> '6E5614'; unknown airlines keep their call sign."""
    cs = (callsign or "").strip().upper()
    if len(cs) > 3 and cs[:3] in ICAO_TO_IATA and cs[3:].isdigit():
        return ICAO_TO_IATA[cs[:3]] + cs[3:]
    return cs or None


def angle_diff(a, b):
    return abs((a - b + 180) % 360 - 180)


def flight_phase(alt_ft, vrate_fpm, track, dist_km, bearing_from_airport, on_ground=False):
    """Arriving, Departing, Overflying or On ground, from height, climb rate and direction."""
    if on_ground or alt_ft < 100:
        return "On ground"
    towards_airport = track is not None and angle_diff(track, (bearing_from_airport + 180) % 360) < 70
    if vrate_fpm <= -300 and alt_ft < 15000:          # descending low near BLR, e.g. turning onto final
        return "Arriving"
    if alt_ft < 20000 and towards_airport and vrate_fpm <= 300:
        return "Arriving"
    if alt_ft < 30000 and vrate_fpm > 300:            # climbing out (may turn back over BLR)
        return "Departing"
    return "Overflying"


def validate_positions(event):
    if not isinstance(event, dict) or not isinstance(event.get("aircraft"), list):
        raise ValidationError("message has no aircraft list")
    if not event.get("snapshot_time"):
        raise ValidationError("message has no snapshot_time")
    for a in event["aircraft"]:
        if not a.get("hex"):
            raise ValidationError("aircraft without hex id")
        lat, lon = a.get("lat"), a.get("lon")
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)) \
                or not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise ValidationError(f"aircraft {a['hex']}: bad position {lat}, {lon}")


def transform_positions(event):
    """ADS-B snapshot -> aircraft within MAP_RADIUS_KM of BLR, with distance, bearing and phase."""
    ap = config.AIRPORT
    out = []
    for a in event["aircraft"]:
        on_ground = a.get("alt_baro") == "ground"
        alt = 0 if on_ground else int(a.get("alt_baro") or a.get("alt_geom") or 0)
        dist = distance_km(ap["lat"], ap["lon"], a["lat"], a["lon"])
        if dist > config.MAP_RADIUS_KM:
            continue
        brg = bearing_deg(ap["lat"], ap["lon"], a["lat"], a["lon"])
        track = a.get("track")
        vrate = int(a.get("baro_rate") or a.get("geom_rate") or 0)
        callsign = (a.get("flight") or "").strip() or None
        out.append({
            "hex": a["hex"].lower(), "callsign": callsign, "flight_iata": flight_from_callsign(callsign),
            "aircraft": a.get("t"), "lat": round(a["lat"], 5), "lon": round(a["lon"], 5),
            "alt_ft": alt, "gs_kt": round(a.get("gs") or 0), "track": round(track) if track is not None else None,
            "vrate_fpm": vrate, "dist_km": round(dist, 1), "bearing": round(brg),
            "phase": flight_phase(alt, vrate, track, dist, brg, on_ground),
        })
    return {"snapshot_time": parse_time(event["snapshot_time"]), "aircraft": out}
