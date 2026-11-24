"""Web dashboard: public board, flight operations and live map (Flask).

    python web_dashboard.py         # then open http://localhost:5000

The pages are plain HTML + JavaScript (templates/, static/). Every few seconds they
call the JSON endpoints below, which read the PostgreSQL views as the read-only user
dashboard_ro, so the browser always shows what the consumers and Flink stored last.

    /                 public board          /api/public
    /ops              flight operations     /api/ops
    /airlines         airlines and weather  /api/ops
    /stream           stream and health     /api/ops
    /map              live airspace map     /api/positions
"""
import datetime as dt
import decimal

import psycopg
from flask import Flask, jsonify, render_template, request
from psycopg.rows import dict_row
from werkzeug.exceptions import HTTPException

import config

app = Flask(__name__)


@app.after_request
def no_cache(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.errorhandler(Exception)
def handle_error(error):
    if isinstance(error, HTTPException):
        return error
    app.logger.exception("Dashboard request failed")
    if request.path.startswith("/api/"):
        return jsonify(error="Data unavailable"), 503
    return "Internal Server Error", 500


def clean(value):
    """Make database values JSON-friendly."""
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return value


def query(conn, sql, one=False):
    rows = conn.execute(sql).fetchall()
    rows = [{k: clean(v) for k, v in r.items()} for r in rows]
    return (rows[0] if rows else {}) if one else rows


def connect():
    return psycopg.connect(config.DASHBOARD_DSN, row_factory=dict_row)


def stamp(data):
    data["generated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    return jsonify(data)


# ---------------------------------------------------------------- pages
@app.get("/")
def public_page():
    return render_template("public.html", page="public")


@app.get("/ops")
def ops_page():
    return render_template("ops.html", page="ops")


@app.get("/airlines")
def airlines_page():
    return render_template("airlines.html", page="airlines")


@app.get("/stream")
def stream_page():
    return render_template("stream.html", page="stream")


@app.get("/map")
def map_page():
    return render_template("map.html", page="map", airport=config.AIRPORT)


# ---------------------------------------------------------------- data
@app.get("/api/public")
def api_public():
    with connect() as conn:
        return stamp({
            "summary": query(conn, "SELECT * FROM v_public_summary", one=True),
            "weather": query(conn, "SELECT * FROM v_public_weather_now", one=True),
            "arrivals": query(conn, "SELECT * FROM v_public_arrivals ORDER BY sched LIMIT 30"),
            "departures": query(conn, "SELECT * FROM v_public_departures ORDER BY sched LIMIT 30"),
            "status": query(conn, "SELECT * FROM v_public_status_next3h ORDER BY flights DESC"),
            "airspace": query(conn, "SELECT * FROM v_positions_summary", one=True),
        })


@app.get("/api/ops")
def api_ops():
    with connect() as conn:
        return stamp({
            "now": query(conn, "SELECT * FROM v_ops_now", one=True),
            "runways": query(conn, "SELECT * FROM v_ops_runways ORDER BY runway"),
            "hourly": query(conn, "SELECT * FROM v_ops_hourly ORDER BY hour_ist"),
            "upcoming": query(conn, "SELECT * FROM v_ops_upcoming ORDER BY expected_at LIMIT 25"),
            "delay_buckets": query(conn, "SELECT * FROM v_ops_delay_buckets"),
            "airlines": query(conn, "SELECT * FROM v_ops_airlines ORDER BY flights_operated DESC"),
            "weather_history": query(conn, "SELECT * FROM v_ops_weather_history ORDER BY obs_ist"),
            "pipeline": query(conn, "SELECT * FROM v_pipeline_health ORDER BY consumer, topic"),
            "airspace": query(conn, "SELECT * FROM v_positions_summary", one=True),
            "flink": {
                "now": query(conn, "SELECT * FROM v_flink_now", one=True),
                "rolling": query(conn, "SELECT * FROM v_flink_runway_rolling ORDER BY step_ist, runway"),
                "hourly": query(conn, "SELECT * FROM v_flink_hourly ORDER BY hour_ist, movement"),
                "check": query(conn, "SELECT * FROM v_flink_check ORDER BY hour_ist DESC LIMIT 12"),
            },
        })


@app.get("/api/positions")
def api_positions():
    with connect() as conn:
        trails = {}
        for t in query(conn, "SELECT hex, lat, lon FROM v_position_trails ORDER BY hex, seen_at"):
            trails.setdefault(t["hex"], []).append([t["lat"], t["lon"]])
        return stamp({
            "airport": config.AIRPORT,
            "radius_km": config.MAP_RADIUS_KM,
            "aircraft": query(conn, "SELECT * FROM v_positions_now ORDER BY dist_km"),
            "trails": trails,
            "summary": query(conn, "SELECT * FROM v_positions_summary", one=True),
            "runways": query(conn, "SELECT runway, used_for, active_direction FROM v_ops_runways ORDER BY runway"),
            "runway_ends": {r["name"]: r["ends"] for r in config.RUNWAYS},
            "weather": query(conn, "SELECT wind_dir, wind_kt, flight_category FROM v_ops_now", one=True),
        })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=config.DASHBOARD_PORT, debug=False)
