"""Offline data used when the flight and weather APIs cannot be reached.

* metar_at(t)            - a METAR for BLR at time t, in the same JSON shape as the
                           aviationweather.gov API (knots, statute miles, feet).
* schedule_for(date)     - the day's arrivals and departures with their delays.
* flight_state(f, now)   - what a flight board would show for that flight at `now`.

A seeded random generator is used, so the same day always gives the same flights
and weather, and the flight delays follow the weather (fog and storms cause delays).
"""
import math
from datetime import datetime, timedelta, timezone

import config
from airport_transform import (IST, active_end, ceiling_ft, flight_category, runway_for,
                               visibility_km)


# ---------------------------------------------------------------------------
# Random helpers
# ---------------------------------------------------------------------------
def hash_str(s):
    """FNV-1a 32-bit hash, used as the random seed."""
    h = 2166136261
    for ch in s:
        h = ((h ^ ord(ch)) * 16777619) & 0xFFFFFFFF
    return h


def make_rng(seed):
    """mulberry32: small pseudo-random generator returning floats in [0, 1)."""
    state = seed & 0xFFFFFFFF

    def rng():
        nonlocal state
        state = (state + 0x6D2B79F5) & 0xFFFFFFFF
        t = ((state ^ (state >> 15)) * (1 | state)) & 0xFFFFFFFF
        t = ((t + (((t ^ (t >> 7)) * (61 | t)) & 0xFFFFFFFF)) ^ t) & 0xFFFFFFFF
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296

    return rng


def pick(r, items, weights):
    x = r() * sum(weights)
    for item, w in zip(items, weights):
        x -= w
        if x < 0:
            return item
    return items[-1]


# ---------------------------------------------------------------------------
# Weather: one METAR every 30 minutes
# ---------------------------------------------------------------------------
def _day_weather(date):
    """Things that hold for a whole day: morning fog, an afternoon shower, a storm."""
    r = make_rng(hash_str(f"VOBL-wx-{date.isoformat()}"))
    return {
        "fog": r() < 0.30,
        "fog_depth": 400 + r() * 1400,          # lowest visibility in metres
        "rain": r() < 0.30,
        "storm": r() < 0.10,
        "wind_dir": 80 + r() * 40,              # November: easterly winds
        "wind": 6 + r() * 6,
        "tmax": 26 + r() * 3,
        "tmin": 16.5 + r() * 2.5,
        "dew": 14.5 + r() * 2.5,
        "qnh": 1013 + r() * 4,
    }


def metar_at(t):
    """METAR for the half hour that contains t (any timezone)."""
    t = t.astimezone(timezone.utc)
    t = t.replace(minute=0 if t.minute < 30 else 30, second=0, microsecond=0)
    local = t.astimezone(IST)
    d = _day_weather(local.date())
    r = make_rng(hash_str(f"VOBL-obs-{t.isoformat()}"))
    h = local.hour + local.minute / 60

    k = math.cos(2 * math.pi * (h - 14.5) / 24)                     # warmest at 2:30 pm
    temp = (d["tmax"] + d["tmin"]) / 2 + (d["tmax"] - d["tmin"]) / 2 * k + (r() - .5)
    dew = d["dew"] + (r() - .5) * 1.2
    wind = d["wind"] * (0.55 + 0.45 * (k + 1) / 2) + (r() - .5) * 3  # windier by day
    wdir = (d["wind_dir"] + (r() - .5) * 30) % 360
    gust = None
    vis = 9999
    wx = ""
    clouds = [("SCT", 2000), ("BKN", 9000)] if r() < 0.6 else [("FEW", 2500)]

    if d["fog"] and 3.5 <= h < 9:                                   # fog thickest near 6:30 am
        depth = 1 - abs(h - 6.5) / 3
        vis = max(d["fog_depth"], 5000 - depth * (5000 - d["fog_depth"]))
        wx = "FG" if vis < 1000 else "BR"
        dew = temp - 0.5 - (r() * 0.5)
        if vis < 1000:
            clouds = [("BKN", 300 + round(r() * 3) * 100)]
        wind = min(wind, 4)
    if d["rain"] and 14 <= h < 18 and r() < 0.7:
        wx, vis = "-RA", 5000 + r() * 2000
        clouds = [("SCT", 1500), ("BKN", 2500 + round(r() * 5) * 100)]
    if d["storm"] and 16 <= h < 18:
        wx, vis, gust = "TSRA", 2500 + r() * 1500, round(26 + r() * 10)
        clouds = [("FEW", 3000, "CB"), ("BKN", 1200)]
        wind = 14 + r() * 6
        wdir = (wdir + 90 + r() * 60) % 360                         # storm winds swing round

    temp, dew = round(temp), round(min(dew, temp))
    wind, wdir = max(0, round(wind)), round(wdir / 10) * 10 % 360 or 360
    vis = int(vis // 100 * 100) if vis < 9999 else 9999
    qnh = round(d["qnh"] - 1.5 * math.sin(2 * math.pi * (h - 4) / 12))

    cloud_txt = " ".join(f"{c[0]}{c[1] // 100:03d}{c[2] if len(c) > 2 else ''}" for c in clouds)
    wind_txt = f"{wdir:03d}{wind:02d}{'G%02d' % gust if gust else ''}KT" if wind else "00000KT"
    temp_txt = f"{temp:02d}/{dew:02d}"
    raw = (f"METAR VOBL {t:%d%H%M}Z {wind_txt} {vis:04d} {wx + ' ' if wx else ''}{cloud_txt} "
           f"{temp_txt} Q{qnh} {'NOSIG' if not wx else 'BECMG'}").replace("  ", " ")
    return {
        "icaoId": "VOBL",
        "reportTime": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "temp": temp, "dewp": dew,
        "wdir": wdir if wind else 0, "wspd": wind, "wgst": gust,
        "visib": "6+" if vis >= 9999 else round(vis / 1609.344, 2),
        "altim": qnh,
        "wxString": wx or None,
        "clouds": [{"cover": c[0], "base": c[1]} for c in clouds],
        "rawOb": raw,
    }


def conditions_at(t):
    """Flight category, weather and runway end at time t (used to delay flights)."""
    m = metar_at(t)
    vis = visibility_km(m["visib"])
    cat = flight_category(vis, ceiling_ft(m["clouds"]))
    return cat, m["wxString"] or "", active_end(m["wdir"] or None, m["wspd"])


# ---------------------------------------------------------------------------
# Flight schedule
# ---------------------------------------------------------------------------
CITIES = {
    "DEL": "Delhi", "BOM": "Mumbai", "HYD": "Hyderabad", "MAA": "Chennai", "CCU": "Kolkata",
    "GOI": "Goa", "PNQ": "Pune", "AMD": "Ahmedabad", "COK": "Kochi", "JAI": "Jaipur",
    "LKO": "Lucknow", "GAU": "Guwahati", "TRV": "Thiruvananthapuram", "BBI": "Bhubaneswar",
    "PAT": "Patna", "IXC": "Chandigarh", "VNS": "Varanasi", "IXE": "Mangaluru",
    "VGA": "Vijayawada", "CJB": "Coimbatore", "NAG": "Nagpur", "IDR": "Indore",
    "SXR": "Srinagar", "IXR": "Ranchi", "HBX": "Hubballi", "IXG": "Belagavi",
    "KLH": "Kolhapur", "MYQ": "Mysuru",
    "DXB": "Dubai", "DOH": "Doha", "SIN": "Singapore", "AUH": "Abu Dhabi", "FRA": "Frankfurt",
    "LHR": "London Heathrow", "BKK": "Bangkok", "SHJ": "Sharjah", "KUL": "Kuala Lumpur",
}
DOMESTIC = ["DEL", "BOM", "HYD", "MAA", "CCU", "GOI", "PNQ", "AMD", "COK", "JAI", "LKO", "GAU",
            "TRV", "BBI", "PAT", "IXC", "VNS", "IXE", "VGA", "CJB", "NAG", "IDR", "SXR", "IXR"]
DOMESTIC_W = [15, 13, 7, 5, 6, 5, 6, 5, 5, 3, 3, 3, 3, 3, 2, 2, 2, 2, 2, 2, 2, 2, 1, 1]

# code, name, share of flights, routes, aircraft, terminal
AIRLINES = [
    ("6E", "IndiGo", 44, None, ["A320neo", "A321neo", "ATR 72"], "T1"),
    ("AI", "Air India", 13, None, ["A320neo", "A321neo"], "T2"),
    ("IX", "Air India Express", 12, None, ["B737-8"], "T2"),
    ("QP", "Akasa Air", 10, None, ["B737-8"], "T1"),
    ("SG", "SpiceJet", 3, None, ["B737-800"], "T1"),
    ("9I", "Alliance Air", 2, ["MYQ", "HBX", "IXG"], ["ATR 72"], "T1"),
    ("S5", "Star Air", 2, ["HBX", "IXG", "KLH"], ["E175"], "T1"),
    ("EK", "Emirates", 2, ["DXB"], ["B777-300ER"], "T2"),
    ("QR", "Qatar Airways", 2, ["DOH"], ["A350-900"], "T2"),
    ("SQ", "Singapore Airlines", 2, ["SIN"], ["A350-900"], "T2"),
    ("EY", "Etihad Airways", 1, ["AUH"], ["B787-9"], "T2"),
    ("G9", "Air Arabia", 2, ["SHJ"], ["A320"], "T2"),
    ("TG", "Thai Airways", 1, ["BKK"], ["A330-300"], "T2"),
    ("MH", "Malaysia Airlines", 1, ["KUL"], ["A330-300"], "T2"),
    ("LH", "Lufthansa", 0.5, ["FRA"], ["A350-900"], "T2"),
    ("BA", "British Airways", 0.5, ["LHR"], ["B787-9"], "T2"),
]
# Share of the day's flights in each hour (IST, 0-23). Busy early morning and evening.
HOUR_W = [5, 3, 2, 2, 3, 6, 8, 8, 7, 6, 6, 6, 5, 5, 6, 6, 6, 7, 8, 8, 8, 7, 7, 6]
INTL_HOUR_W = [6, 6, 5, 4, 3, 2, 1, 1, 3, 4, 3, 2, 1, 1, 1, 1, 2, 2, 3, 4, 5, 6, 6, 6]
FLIGHTS_PER_DAY = 330          # per direction, so about 660 movements a day


def schedule_for(date):
    """All arrivals and departures scheduled on one IST calendar day."""
    r = make_rng(hash_str(f"BLR-schedule-{date.isoformat()}"))
    midnight = datetime(date.year, date.month, date.day, tzinfo=IST)
    flights, used = [], set()
    codes = [a[0] for a in AIRLINES]
    shares = [a[2] for a in AIRLINES]
    for movement in ("DEP", "ARR"):
        n = FLIGHTS_PER_DAY + int((r() - .5) * 20)
        for _ in range(n):
            code, name, _share, routes, fleet, terminal = AIRLINES[codes.index(pick(r, codes, shares))]
            intl = code in ("EK", "QR", "SQ", "EY", "G9", "TG", "MH", "LH", "BA")
            other = pick(r, routes, [1] * len(routes)) if routes else pick(r, DOMESTIC, DOMESTIC_W)
            hour = pick(r, list(range(24)), INTL_HOUR_W if intl else HOUR_W)
            sched = midnight + timedelta(hours=hour, minutes=int(r() * 12) * 5)
            number = f"{code}{int(100 + r() * 900) if intl or code in ('9I', 'S5') else int(100 + r() * 6800)}"
            while (number, movement) in used:
                number = f"{code}{int(number[2:]) + 1}"
            used.add((number, movement))

            # Delay: most flights close to time, some a long way out; worse in bad weather.
            cat, wx, _ = conditions_at(sched)
            delay = -6 + r() * 18 if r() < 0.72 else 12 - 26 * math.log(1 - r())
            delay += {"VFR": 0, "MVFR": 4, "IFR": 15, "LIFR": 35}[cat] * r()
            if "TS" in wx:
                delay += 25 * r()
            delay += 6 * r() * HOUR_W[hour] / 8                     # congestion at peak hours
            flights.append({
                "flight_id": f"{date:%Y%m%d}-{number}-{movement}",
                "flight_iata": number, "airline_iata": code, "airline_name": name,
                "movement": movement,
                "origin_iata": other if movement == "ARR" else "BLR",
                "dest_iata": "BLR" if movement == "ARR" else other,
                "origin_city": CITIES[other] if movement == "ARR" else "Bengaluru",
                "dest_city": "Bengaluru" if movement == "ARR" else CITIES[other],
                "aircraft": pick(r, fleet, [1] * len(fleet)),
                "terminal": terminal,
                "gate": f"{terminal[1]}{int(1 + r() * 40):02d}",
                "sched": sched,
                "delay": round(delay),
                "taxi": round(8 + r() * 10 if movement == "DEP" else 5 + r() * 6),
                "cancelled": r() < 0.008,
            })
    return flights


def flight_state(f, now):
    """Status, estimated/actual times and runway for one simulated flight at `now`."""
    sched, delay, taxi = f["sched"], f["delay"], f["taxi"]
    known = now >= sched - timedelta(hours=3)          # delays are announced 3 h ahead
    est = sched + timedelta(minutes=delay) if known else sched
    out = {"est_time": est, "actual_time": None, "runway_time": None, "runway": None}

    if f["cancelled"]:
        return {**out, "status": "Cancelled" if known else "Scheduled", "est_time": None if known else sched}

    if f["movement"] == "DEP":
        wheels_up = sched + timedelta(minutes=delay + taxi)
        if now >= wheels_up:
            status = "Departed"
            out.update(actual_time=sched + timedelta(minutes=delay), runway_time=wheels_up)
        elif now >= est:
            status = "Taxiing"
            out["actual_time"] = est
        elif now >= est - timedelta(minutes=10):
            status = "Gate Closed"
        elif now >= est - timedelta(minutes=40):
            status = "Boarding"
        else:
            status = "Delayed" if known and delay >= config.ON_TIME_MINUTES else "Scheduled"
        rwy_time = wheels_up
    else:
        touchdown = sched + timedelta(minutes=delay - taxi)
        on_block = sched + timedelta(minutes=delay)
        if now >= on_block:
            status = "Arrived"
            out.update(actual_time=on_block, runway_time=touchdown)
        elif now >= touchdown:
            status = "Landed"
            out["runway_time"] = touchdown
        elif now >= touchdown - timedelta(minutes=15):
            status = "Approaching"
        elif known and delay >= config.ON_TIME_MINUTES:
            status = "Delayed"
        elif now >= touchdown - timedelta(minutes=90):
            status = "En Route"
        else:
            status = "Scheduled"
        rwy_time = touchdown

    if out["runway_time"] is not None or known:
        _, _, end = conditions_at(rwy_time)
        out["runway"] = runway_for(f["movement"], end)
    return {**out, "status": status}


# ---------------------------------------------------------------------------
# Aircraft positions: where each arriving / departing flight is right now
# ---------------------------------------------------------------------------
# Approximate airport coordinates, used only to point each flight the right way.
COORDS = {
    "DEL": (28.556, 77.100), "BOM": (19.090, 72.866), "HYD": (17.240, 78.429), "MAA": (12.994, 80.171),
    "CCU": (22.655, 88.447), "GOI": (15.381, 73.831), "PNQ": (18.582, 73.920), "AMD": (23.077, 72.635),
    "COK": (10.152, 76.402), "JAI": (26.824, 75.812), "LKO": (26.761, 80.889), "GAU": (26.106, 91.586),
    "TRV": (8.482, 76.920), "BBI": (20.244, 85.818), "PAT": (25.591, 85.088), "IXC": (30.674, 76.789),
    "VNS": (25.452, 82.859), "IXE": (12.961, 74.890), "VGA": (16.530, 80.797), "CJB": (11.030, 77.043),
    "NAG": (21.092, 79.047), "IDR": (22.722, 75.801), "SXR": (33.987, 74.774), "IXR": (23.314, 85.322),
    "HBX": (15.362, 75.085), "IXG": (15.859, 74.618), "KLH": (16.665, 74.289), "MYQ": (12.230, 76.656),
    "DXB": (25.253, 55.366), "DOH": (25.273, 51.608), "SIN": (1.364, 103.992), "AUH": (24.433, 54.651),
    "FRA": (50.038, 8.562), "LHR": (51.470, -0.454), "BKK": (13.690, 100.750), "SHJ": (25.329, 55.517),
    "KUL": (2.746, 101.710),
}
BLR_ELEVATION_FT = 3000


def _offset(dist_km, brg):
    """(east, north) in km for a distance along a bearing."""
    r = math.radians(brg)
    return dist_km * math.sin(r), dist_km * math.cos(r)


def _to_latlon(east, north):
    lat0, lon0 = config.AIRPORT["lat"], config.AIRPORT["lon"]
    return lat0 + north / 111.0, lon0 + east / (111.0 * math.cos(math.radians(lat0)))


def _path_point(near_brg, near_km, far_brg, far_km, d):
    """Point d km from the airport on a path: straight along near_brg up to near_km, then
    bending towards far_brg (reached at far_km). Returns (east, north)."""
    if d <= near_km:
        return _offset(d, near_brg)
    ax, ay = _offset(near_km, near_brg)
    bx, by = _offset(far_km, far_brg)
    f = min(1.0, (d - near_km) / (far_km - near_km))
    return ax + (bx - ax) * f, ay + (by - ay) * f


def _bearing_to(iata):
    from airport_transform import bearing_deg
    lat, lon = COORDS[iata]
    return bearing_deg(config.AIRPORT["lat"], config.AIRPORT["lon"], lat, lon)


RUNWAY_OFFSET_KM = {"ARR": 0.94, "DEP": -1.0}   # runway centre lines north/south of the airport reference point


def positions_at(now):
    """ADS-B style aircraft list (same fields as adsb.lol) for flights within ~60 km of BLR."""
    from airport_transform import IATA_TO_ICAO, bearing_deg
    local = now.astimezone(IST)
    flights = []
    for d in (local.date() - timedelta(days=1), local.date(), local.date() + timedelta(days=1)):
        flights += schedule_for(d)
    out = []
    for f in flights:
        if f["cancelled"]:
            continue
        r = make_rng(hash_str(f["flight_id"]))
        _, _, end = conditions_at(f["sched"])
        rwy_brg = 90 if end == "09" else 270                       # direction of landing / take-off
        if f["movement"] == "ARR":
            touchdown = f["sched"] + timedelta(minutes=f["delay"] - f["taxi"])
            m = (touchdown - now).total_seconds() / 60 + (r() - 0.5) * 2.4   # flights on the same minute spread out
            if not 0 < m <= 16:
                continue
            dist = 3.2 * m + 0.3                                   # about 190 km/h on average
            final_brg = (rwy_brg + 180) % 360                      # final approach from the other side
            east, north = _path_point(final_brg, 15, _bearing_to(f["origin_iata"]), 60, dist)
            ahead = _path_point(final_brg, 15, _bearing_to(f["origin_iata"]), 60, max(0.0, dist - 1))
            alt = BLR_ELEVATION_FT + (172 * dist if dist <= 15 else 2580 + 300 * (dist - 15))
            gs, vrate = 135 + 3.5 * min(dist, 40), -700 - r() * 300
        else:
            wheels_up = f["sched"] + timedelta(minutes=f["delay"] + f["taxi"])
            m = (now - wheels_up).total_seconds() / 60 - r() * 1.5
            if not 0 <= m <= 13:
                continue
            dist = 0.5 + 4.6 * m
            east, north = _path_point(rwy_brg, 8, _bearing_to(f["dest_iata"]), 62, dist)
            ahead = _path_point(rwy_brg, 8, _bearing_to(f["dest_iata"]), 62, dist + 1)
            alt = BLR_ELEVATION_FT + min(1900 * m + 300, 24000)
            gs, vrate = 160 + 9 * min(m, 13), 1800 + r() * 700
        # Line the path up with the runway in use: arrivals on the north runway (09L/27R),
        # departures on the south runway (09R/27L), about 0.9 km either side of the reference point.
        shift = RUNWAY_OFFSET_KM["ARR" if f["movement"] == "ARR" else "DEP"]
        lat, lon = _to_latlon(east, north + shift)
        alat, alon = _to_latlon(ahead[0], ahead[1] + shift)
        track = bearing_deg(lat, lon, alat, alon)
        number = f["flight_iata"][2:]
        out.append({
            "hex": f"8{hash_str(f['flight_id']) % 0x3FFFF:05x}",       # Indian ICAO address block
            "flight": f"{IATA_TO_ICAO.get(f['airline_iata'], f['airline_iata'])}{number}",
            "t": {"A320neo": "A20N", "A321neo": "A21N", "ATR 72": "AT76", "B737-8": "B38M",
                  "B737-800": "B738", "E175": "E75L", "B777-300ER": "B77W", "A350-900": "A359",
                  "B787-9": "B789", "A320": "A320", "A330-300": "A333"}.get(f["aircraft"], f["aircraft"]),
            "lat": round(lat, 5), "lon": round(lon, 5), "alt_baro": int(round(alt, -2)),
            "gs": round(gs), "track": round(track, 1), "baro_rate": int(round(vrate, -1)),
        })
    return out
