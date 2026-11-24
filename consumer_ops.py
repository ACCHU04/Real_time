"""Consumer 2 - flight operations (consumer group ops_control_group).

Reads flights.raw and weather.raw and keeps what the operations team needs:
runway, aircraft, wheels-up/touchdown times, delay bands, and the aviation weather
(flight category, ceiling, head/crosswind on the runway in use, capacity factor).
The views built on these tables give runway utilisation and how busy the airport is.

    python consumer_ops.py              # run until Ctrl+C
    python consumer_ops.py --max 500    # stop after 500 messages
"""
import argparse
import logging
from datetime import datetime

import config
from airport_transform import runway_for
from consumer_common import run

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def ops_flight_view(m, active_end=config.PREFERRED_END):
    return {
        "flight_id": m["flight_id"], "flight_iata": m["flight_iata"], "airline_iata": m["airline_iata"],
        "airline_name": m["airline_name"], "movement": m["movement"],
        "other_iata": m["origin_iata"] if m["movement"] == "ARR" else m["dest_iata"],
        "aircraft": m["aircraft"],
        # Live data has no runway: use the runway for this movement type at the end in use now.
        "runway": m["runway"] or runway_for(m["movement"], active_end),
        "sched": m["sched"], "est": m["est"], "actual": m["actual"], "runway_time": m["runway_time"],
        "status": m["status"], "delay_min": m["delay_min"], "delay_bucket": m["bucket"],
        "on_time": m["on_time"], "completed": m["completed"],
    }


def ops_weather_view(w):
    keys = ["obs_time", "raw", "temp", "dew", "wind_dir", "wind_kt", "gust_kt", "vis_km", "ceiling_ft",
            "qnh", "wx", "category", "active_end", "headwind_kt", "crosswind_kt", "crosswind_alert",
            "capacity_factor", "capacity_per_runway"]
    return {k: w[k] for k in keys}


def save_flight(conn, event, model):
    row = conn.execute("SELECT active_end FROM ops_weather ORDER BY obs_time DESC LIMIT 1").fetchone()
    v = ops_flight_view(model, row[0] if row else config.PREFERRED_END)
    conn.execute(
        """INSERT INTO ops_flights (flight_id, flight_iata, airline_iata, airline_name, movement, other_iata,
                                    aircraft, runway, sched, est, actual, runway_time, status, delay_min,
                                    delay_bucket, on_time, completed, emitted_at)
           VALUES (%(flight_id)s, %(flight_iata)s, %(airline_iata)s, %(airline_name)s, %(movement)s,
                   %(other_iata)s, %(aircraft)s, %(runway)s, %(sched)s, %(est)s, %(actual)s,
                   %(runway_time)s, %(status)s, %(delay_min)s, %(delay_bucket)s, %(on_time)s,
                   %(completed)s, %(emitted_at)s)
           ON CONFLICT (flight_id) DO UPDATE SET
             runway = EXCLUDED.runway, est = EXCLUDED.est, actual = EXCLUDED.actual,
             runway_time = EXCLUDED.runway_time, status = EXCLUDED.status,
             delay_min = EXCLUDED.delay_min, delay_bucket = EXCLUDED.delay_bucket,
             on_time = EXCLUDED.on_time, completed = EXCLUDED.completed,
             emitted_at = EXCLUDED.emitted_at, updated_at = now()
           WHERE ops_flights.emitted_at <= EXCLUDED.emitted_at""",
        {**v, "emitted_at": datetime.fromisoformat(event["emitted_at"])})


def save_weather(conn, event, model):
    conn.execute(
        """INSERT INTO ops_weather (obs_time, raw_metar, temp_c, dew_c, wind_dir, wind_kt, gust_kt, vis_km,
                                    ceiling_ft, qnh_hpa, wx, category, active_end, headwind_kt, crosswind_kt,
                                    crosswind_alert, capacity_factor, capacity_per_runway)
           VALUES (%(obs_time)s, %(raw)s, %(temp)s, %(dew)s, %(wind_dir)s, %(wind_kt)s, %(gust_kt)s,
                   %(vis_km)s, %(ceiling_ft)s, %(qnh)s, %(wx)s, %(category)s, %(active_end)s,
                   %(headwind_kt)s, %(crosswind_kt)s, %(crosswind_alert)s, %(capacity_factor)s,
                   %(capacity_per_runway)s)
           ON CONFLICT (obs_time) DO NOTHING""", ops_weather_view(model))


def describe_flight(m):
    if not m["completed"]:
        return ""                     # log only runway movements, to keep the output readable
    when = m["runway_time"] or m["actual"]
    verb = "TAKEOFF" if m["movement"] == "DEP" else "LANDING"
    return (f"{verb} {m['flight_iata']:<7} rwy {m['runway'] or '-':<4} {when:%H:%M}  "
            f"{m['aircraft'] or '':<10} delay {m['delay_min']:>4} min  [{m['bucket']}]")


def describe_weather(w):
    return (f"METAR {w['obs_time']:%d %b %H:%M} IST  {w['category']:<4}  vis {w['vis_km']:.1f} km  "
            f"ceiling {w['ceiling_ft'] or 'none'}  wind {w['wind_dir'] or 0:03d}/{w['wind_kt']} kt  "
            f"rwy {w['active_end']} head {w['headwind_kt']:+.0f} cross {w['crosswind_kt']:.0f} kt  "
            f"capacity {w['capacity_per_runway']}/rwy/h{'  CROSSWIND ALERT' if w['crosswind_alert'] else ''}")


def main():
    parser = argparse.ArgumentParser(description="Flight operations consumer")
    parser.add_argument("--max", type=int, help="stop after this many messages")
    parser.add_argument("--stop-when-idle", type=float, help="stop after this many idle seconds")
    args = parser.parse_args()
    run("ops", config.GROUP_OPS, {
        config.TOPIC_FLIGHTS: (save_flight, describe_flight),
        config.TOPIC_WEATHER: (save_weather, describe_weather),
    }, args.max, args.stop_when_idle)


if __name__ == "__main__":
    main()
