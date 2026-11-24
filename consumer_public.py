"""Consumer 1 - general public (consumer group public_board_group).

Reads flights.raw, weather.raw and positions.raw and keeps what a passenger needs:
the arrivals and departures board (time, flight, from/to, expected time, status,
gate), the current weather in plain words with a travel advisory, and the live
positions of aircraft around the airport for the map. Runway allocation and
aviation weather codes are left to the operations consumer.

    python consumer_public.py              # run until Ctrl+C
    python consumer_public.py --max 500    # stop after 500 messages
"""
import argparse
import logging
from datetime import datetime

import config
from airport_transform import compass
from consumer_common import run

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def public_flight_view(m):
    return {
        "flight_id": m["flight_id"], "flight_iata": m["flight_iata"], "airline_name": m["airline_name"],
        "movement": m["movement"],
        "city": m["origin_city"] if m["movement"] == "ARR" else m["dest_city"],
        "sched": m["sched"], "est": m["est"], "actual": m["actual"], "status": m["status"],
        "terminal": m["terminal"], "gate": m["gate"] if m["movement"] == "DEP" else None,
        "delay_min": m["delay_min"], "on_time": m["on_time"], "completed": m["completed"],
    }


def advisory(w):
    if "TS" in w["wx"]:
        return "Thunderstorm at the airport: delays likely, check your flight status"
    if w["category"] in ("IFR", "LIFR"):
        return f"Low visibility ({w['vis_km']:.1f} km): some flights may be delayed"
    if w["crosswind_alert"]:
        return "Strong winds: some flights may be delayed"
    if "RA" in w["wx"] or "DZ" in w["wx"]:
        return "Rain: allow extra time to reach the airport"
    return "No weather disruption expected"


def public_weather_view(w):
    return {
        "obs_time": w["obs_time"], "temp_c": w["temp"], "feels_c": w["feels"], "rh_pct": w["rh"],
        "wind_kmh": w["wind_kmh"], "wind_dir": compass(w["wind_dir"]) if w["wind_kt"] else "Calm",
        "vis_km": w["vis_km"], "condition": w["condition"], "advisory": advisory(w),
    }


def save_flight(conn, event, model):
    v = public_flight_view(model)
    # Upsert; an older event (by emitted_at) never overwrites a newer one.
    conn.execute(
        """INSERT INTO public_flights (flight_id, flight_iata, airline_name, movement, city, sched, est,
                                       actual, status, terminal, gate, delay_min, on_time, completed, emitted_at)
           VALUES (%(flight_id)s, %(flight_iata)s, %(airline_name)s, %(movement)s, %(city)s, %(sched)s,
                   %(est)s, %(actual)s, %(status)s, %(terminal)s, %(gate)s, %(delay_min)s, %(on_time)s,
                   %(completed)s, %(emitted_at)s)
           ON CONFLICT (flight_id) DO UPDATE SET
             est = EXCLUDED.est, actual = EXCLUDED.actual, status = EXCLUDED.status,
             gate = EXCLUDED.gate, delay_min = EXCLUDED.delay_min, on_time = EXCLUDED.on_time,
             completed = EXCLUDED.completed, emitted_at = EXCLUDED.emitted_at, updated_at = now()
           WHERE public_flights.emitted_at <= EXCLUDED.emitted_at""",
        {**v, "emitted_at": datetime.fromisoformat(event["emitted_at"])})


def save_weather(conn, event, model):
    v = public_weather_view(model)
    conn.execute(
        """INSERT INTO public_weather (obs_time, temp_c, feels_c, rh_pct, wind_kmh, wind_dir, vis_km,
                                       condition, advisory)
           VALUES (%(obs_time)s, %(temp_c)s, %(feels_c)s, %(rh_pct)s, %(wind_kmh)s, %(wind_dir)s,
                   %(vis_km)s, %(condition)s, %(advisory)s)
           ON CONFLICT (obs_time) DO NOTHING""", v)


def save_positions(conn, event, model):
    """Latest position per aircraft for the map, plus a short history for the trails."""
    snap = model["snapshot_time"]
    rows = [{**a, "seen_at": snap} for a in model["aircraft"]]
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO aircraft_positions (hex, callsign, flight_iata, aircraft, lat, lon, alt_ft, gs_kt,
                                               track, vrate_fpm, dist_km, bearing, phase, seen_at)
               VALUES (%(hex)s, %(callsign)s, %(flight_iata)s, %(aircraft)s, %(lat)s, %(lon)s, %(alt_ft)s,
                       %(gs_kt)s, %(track)s, %(vrate_fpm)s, %(dist_km)s, %(bearing)s, %(phase)s, %(seen_at)s)
               ON CONFLICT (hex) DO UPDATE SET
                 callsign = EXCLUDED.callsign, flight_iata = EXCLUDED.flight_iata, aircraft = EXCLUDED.aircraft,
                 lat = EXCLUDED.lat, lon = EXCLUDED.lon, alt_ft = EXCLUDED.alt_ft, gs_kt = EXCLUDED.gs_kt,
                 track = EXCLUDED.track, vrate_fpm = EXCLUDED.vrate_fpm, dist_km = EXCLUDED.dist_km,
                 bearing = EXCLUDED.bearing, phase = EXCLUDED.phase, seen_at = EXCLUDED.seen_at
               WHERE aircraft_positions.seen_at <= EXCLUDED.seen_at""", rows)
        cur.executemany(
            """INSERT INTO position_history (hex, seen_at, lat, lon, alt_ft) VALUES
               (%(hex)s, %(seen_at)s, %(lat)s, %(lon)s, %(alt_ft)s) ON CONFLICT DO NOTHING""", rows)
    conn.execute("DELETE FROM position_history WHERE seen_at < %s - interval '15 minutes'", (snap,))


def describe_positions(m):
    phases = {}
    for a in m["aircraft"]:
        phases[a["phase"]] = phases.get(a["phase"], 0) + 1
    detail = ", ".join(f"{v} {k.lower()}" for k, v in sorted(phases.items()))
    return f"POSITIONS {m['snapshot_time']:%H:%M:%S}  {len(m['aircraft'])} aircraft within 60 km: {detail}"


def describe_flight(m):
    other = m["origin_iata"] if m["movement"] == "ARR" else m["dest_iata"]
    late = f"late {m['delay_min']} min" if not m["on_time"] and m["status"] != "Cancelled" else ""
    return (f"{m['movement']} {m['flight_iata']:<7} {other:<4} sched {m['sched']:%H:%M} "
            f"exp {m['est']:%H:%M}  {m['status']:<11} {late}").rstrip()


def describe_weather(w):
    v = public_weather_view(w)
    return (f"WEATHER {w['obs_time']:%d %b %H:%M} IST  {v['temp_c']:.0f} C (feels {v['feels_c']:.0f} C)  "
            f"RH {v['rh_pct']}%  wind {v['wind_kmh']:.0f} km/h {v['wind_dir']}  vis {v['vis_km']:.1f} km  "
            f"{v['condition']}  -> {v['advisory']}")


def main():
    parser = argparse.ArgumentParser(description="Public flight board consumer")
    parser.add_argument("--max", type=int, help="stop after this many messages")
    parser.add_argument("--stop-when-idle", type=float, help="stop after this many idle seconds")
    args = parser.parse_args()
    run("public", config.GROUP_PUBLIC, {
        config.TOPIC_FLIGHTS: (save_flight, describe_flight),
        config.TOPIC_WEATHER: (save_weather, describe_weather),
        config.TOPIC_POSITIONS: (save_positions, describe_positions),
    }, args.max, args.stop_when_idle)


if __name__ == "__main__":
    main()
