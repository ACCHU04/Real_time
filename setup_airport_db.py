"""Create the tables and the views that the web dashboard reads. Safe to run more than once.

    python setup_airport_db.py
"""
import psycopg

import config

TABLES = """
-- Runways, copied from config.RUNWAYS so the views can use them.
CREATE TABLE IF NOT EXISTS runways (
    name     TEXT PRIMARY KEY,           -- e.g. 09L/27R
    end_09   TEXT NOT NULL,              -- 09L
    end_27   TEXT NOT NULL,              -- 27R
    use      TEXT NOT NULL CHECK (use IN ('ARR', 'DEP'))
);

-- One row per message per consumer, with the time spent in each stage.
CREATE TABLE IF NOT EXISTS pipeline_events (
    event_id      UUID NOT NULL,
    consumer      TEXT NOT NULL CHECK (consumer IN ('public', 'ops')),
    topic         TEXT NOT NULL,
    source        TEXT NOT NULL CHECK (source IN ('live', 'offline')),
    emitted_at    TIMESTAMPTZ NOT NULL,
    processed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    validate_ms   REAL,
    transform_ms  REAL,
    store_ms      REAL,
    PRIMARY KEY (event_id, consumer)
);

-- Messages that failed validation (also sent to the airport.dlq topic).
CREATE TABLE IF NOT EXISTS dlq_events (
    id           SERIAL PRIMARY KEY,
    consumer     TEXT NOT NULL,
    topic        TEXT NOT NULL,
    kafka_offset BIGINT NOT NULL,
    error        TEXT NOT NULL,
    received_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Consumer 1 (public): flight board and current weather.
CREATE TABLE IF NOT EXISTS public_flights (
    flight_id     TEXT PRIMARY KEY,
    flight_iata   TEXT NOT NULL,
    airline_name  TEXT NOT NULL,
    movement      TEXT NOT NULL CHECK (movement IN ('ARR', 'DEP')),
    city          TEXT,                  -- where it comes from / goes to
    sched         TIMESTAMPTZ NOT NULL,
    est           TIMESTAMPTZ,
    actual        TIMESTAMPTZ,
    status        TEXT NOT NULL,
    terminal      TEXT,
    gate          TEXT,
    delay_min     INTEGER NOT NULL,
    on_time       BOOLEAN NOT NULL,
    completed     BOOLEAN NOT NULL,
    emitted_at    TIMESTAMPTZ NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public_weather (
    obs_time    TIMESTAMPTZ PRIMARY KEY,
    temp_c      REAL NOT NULL,
    feels_c     REAL NOT NULL,
    rh_pct      SMALLINT NOT NULL,
    wind_kmh    REAL NOT NULL,
    wind_dir    TEXT NOT NULL,
    vis_km      REAL NOT NULL,
    condition   TEXT NOT NULL,
    advisory    TEXT NOT NULL
);

-- Consumer 2 (flight operations): runway use, delays and aviation weather.
CREATE TABLE IF NOT EXISTS ops_flights (
    flight_id     TEXT PRIMARY KEY,
    flight_iata   TEXT NOT NULL,
    airline_iata  TEXT,
    airline_name  TEXT NOT NULL,
    movement      TEXT NOT NULL CHECK (movement IN ('ARR', 'DEP')),
    other_iata    TEXT,
    aircraft      TEXT,
    runway        TEXT,
    sched         TIMESTAMPTZ NOT NULL,
    est           TIMESTAMPTZ,
    actual        TIMESTAMPTZ,
    runway_time   TIMESTAMPTZ,           -- wheels-up or touchdown
    status        TEXT NOT NULL,
    delay_min     INTEGER NOT NULL,
    delay_bucket  TEXT NOT NULL,
    on_time       BOOLEAN NOT NULL,
    completed     BOOLEAN NOT NULL,
    emitted_at    TIMESTAMPTZ NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ops_flights_runway_time ON ops_flights (runway_time);
CREATE INDEX IF NOT EXISTS ops_flights_sched ON ops_flights (sched);

-- Live aircraft positions for the map (consumer 1): the latest per aircraft + 15 min of history.
CREATE TABLE IF NOT EXISTS aircraft_positions (
    hex          TEXT PRIMARY KEY,            -- ICAO 24-bit address
    callsign     TEXT,
    flight_iata  TEXT,
    aircraft     TEXT,
    lat          DOUBLE PRECISION NOT NULL,
    lon          DOUBLE PRECISION NOT NULL,
    alt_ft       INTEGER NOT NULL,
    gs_kt        INTEGER,
    track        INTEGER,
    vrate_fpm    INTEGER,
    dist_km      REAL NOT NULL,
    bearing      INTEGER NOT NULL,
    phase        TEXT NOT NULL,
    seen_at      TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS position_history (
    hex      TEXT NOT NULL,
    seen_at  TIMESTAMPTZ NOT NULL,
    lat      DOUBLE PRECISION NOT NULL,
    lon      DOUBLE PRECISION NOT NULL,
    alt_ft   INTEGER NOT NULL,
    PRIMARY KEY (hex, seen_at)
);

-- Written by the Flink job (flink_runway_job.py), kept up to date as events arrive.
CREATE TABLE IF NOT EXISTS flink_runway_5min (
    runway         TEXT NOT NULL,
    bucket_start   TIMESTAMP NOT NULL,         -- IST, start of the 5-minute bucket
    movements      BIGINT NOT NULL,
    avg_delay_min  DOUBLE PRECISION,
    PRIMARY KEY (runway, bucket_start)
);

CREATE TABLE IF NOT EXISTS flink_hourly (
    hour_start     TIMESTAMP NOT NULL,         -- IST
    movement       TEXT NOT NULL,
    movements      BIGINT NOT NULL,
    avg_delay_min  DOUBLE PRECISION,
    late_flights   INTEGER NOT NULL,
    PRIMARY KEY (hour_start, movement)
);

CREATE TABLE IF NOT EXISTS ops_weather (
    obs_time             TIMESTAMPTZ PRIMARY KEY,
    raw_metar            TEXT NOT NULL,
    temp_c               REAL NOT NULL,
    dew_c                REAL NOT NULL,
    wind_dir             SMALLINT,
    wind_kt              SMALLINT NOT NULL,
    gust_kt              SMALLINT,
    vis_km               REAL NOT NULL,
    ceiling_ft           INTEGER,
    qnh_hpa              SMALLINT NOT NULL,
    wx                   TEXT NOT NULL,
    category             TEXT NOT NULL,
    active_end           TEXT NOT NULL,
    headwind_kt          REAL NOT NULL,
    crosswind_kt         REAL NOT NULL,
    crosswind_alert      BOOLEAN NOT NULL,
    capacity_factor      REAL NOT NULL,
    capacity_per_runway  SMALLINT NOT NULL
);
"""

BUSY_CASE = "CASE " + " ".join(
    f"WHEN {{x}} >= {limit} THEN '{label}'" for limit, label in config.BUSY_LEVELS if limit > 0) + \
    f" ELSE '{config.BUSY_LEVELS[-1][1]}' END"

VIEWS = f"""
-- ---------------------------------------------------------------- public
CREATE OR REPLACE VIEW v_public_board AS
SELECT flight_id, movement,
       CASE movement WHEN 'ARR' THEN 'Arrival' ELSE 'Departure' END AS direction,
       to_char(sched AT TIME ZONE 'Asia/Kolkata', 'HH24:MI') AS sched_time,
       flight_iata AS flight, airline_name AS airline, city,
       CASE WHEN status = 'Cancelled' THEN '-'
            ELSE to_char(COALESCE(actual, est) AT TIME ZONE 'Asia/Kolkata', 'HH24:MI') END AS expected,
       status, terminal, gate, delay_min,
       CASE WHEN status = 'Cancelled' THEN 'Cancelled'
            WHEN delay_min >= {config.ON_TIME_MINUTES} THEN 'Late ' || delay_min || ' min'
            ELSE 'On time' END AS remark,
       sched
FROM public_flights
WHERE COALESCE(actual, est, sched) BETWEEN now() - interval '30 minutes' AND now() + interval '4 hours'
   OR (NOT completed AND status <> 'Cancelled' AND sched > now() - interval '6 hours');

CREATE OR REPLACE VIEW v_public_arrivals AS
SELECT * FROM v_public_board WHERE movement = 'ARR';

CREATE OR REPLACE VIEW v_public_departures AS
SELECT * FROM v_public_board WHERE movement = 'DEP';

CREATE OR REPLACE VIEW v_public_summary AS
SELECT
  COUNT(*) FILTER (WHERE movement = 'ARR' AND NOT completed AND status <> 'Cancelled'
                   AND COALESCE(est, sched) BETWEEN now() AND now() + interval '60 minutes') AS arrivals_next_60,
  COUNT(*) FILTER (WHERE movement = 'DEP' AND NOT completed AND status <> 'Cancelled'
                   AND COALESCE(est, sched) BETWEEN now() AND now() + interval '60 minutes') AS departures_next_60,
  COUNT(*) FILTER (WHERE status = 'Delayed') AS delayed_now,
  COUNT(*) FILTER (WHERE status = 'Cancelled' AND sched BETWEEN now() - interval '24 hours'
                   AND now() + interval '12 hours') AS cancelled,
  ROUND(100.0 * COUNT(*) FILTER (WHERE completed AND on_time AND sched > now() - interval '24 hours')
        / NULLIF(COUNT(*) FILTER (WHERE completed AND sched > now() - interval '24 hours'), 0), 1) AS on_time_pct_24h,
  ROUND(AVG(GREATEST(delay_min, 0)) FILTER (WHERE completed AND sched > now() - interval '24 hours'), 1) AS avg_delay_min_24h,
  COUNT(*) FILTER (WHERE completed AND sched > now() - interval '24 hours') AS flights_completed_24h
FROM public_flights;

CREATE OR REPLACE VIEW v_public_status_next3h AS
SELECT status, COUNT(*) AS flights
FROM public_flights
WHERE sched BETWEEN now() AND now() + interval '3 hours'
GROUP BY status;

CREATE OR REPLACE VIEW v_public_weather_now AS
SELECT obs_time AT TIME ZONE 'Asia/Kolkata' AS observed_ist,
       to_char(obs_time AT TIME ZONE 'Asia/Kolkata', 'HH24:MI') AS observed,
       temp_c, feels_c, rh_pct, wind_kmh, wind_dir, vis_km, condition, advisory
FROM public_weather
ORDER BY obs_time DESC
LIMIT 1;

-- ---------------------------------------------------------------- operations
CREATE OR REPLACE VIEW v_ops_weather_now AS
SELECT * FROM ops_weather ORDER BY obs_time DESC LIMIT 1;

CREATE OR REPLACE VIEW v_ops_runways AS
WITH w AS (SELECT * FROM v_ops_weather_now)
SELECT r.name AS runway, r.use AS used_for,
       CASE WHEN w.active_end = '09' THEN r.end_09 ELSE r.end_27 END AS active_direction,
       COUNT(f.*) FILTER (WHERE f.runway_time BETWEEN now() - interval '60 minutes' AND now()) AS movements_last_60,
       COUNT(f.*) FILTER (WHERE NOT f.completed AND f.status <> 'Cancelled'
                          AND COALESCE(f.est, f.sched) BETWEEN now() AND now() + interval '60 minutes') AS planned_next_60,
       w.capacity_per_runway AS capacity_per_hour,
       ROUND(100.0 * COUNT(f.*) FILTER (WHERE f.runway_time BETWEEN now() - interval '60 minutes' AND now())
             / NULLIF(w.capacity_per_runway, 0), 1) AS utilisation_pct,
       {BUSY_CASE.format(x="100.0 * COUNT(f.*) FILTER (WHERE f.runway_time BETWEEN now() - interval '60 minutes' AND now()) / NULLIF(w.capacity_per_runway, 0)")} AS busy_level
FROM runways r
CROSS JOIN w
LEFT JOIN ops_flights f ON f.movement = r.use      -- segregated mode: one runway per movement type
GROUP BY r.name, r.use, r.end_09, r.end_27, w.active_end, w.capacity_per_runway;

CREATE OR REPLACE VIEW v_ops_now AS
WITH w AS (SELECT * FROM v_ops_weather_now),
     r AS (SELECT SUM(movements_last_60) AS mov, SUM(planned_next_60) AS planned,
                  SUM(capacity_per_hour) AS cap FROM v_ops_runways),
     f AS (SELECT
       COUNT(*) FILTER (WHERE movement = 'ARR' AND runway_time > now() - interval '60 minutes' AND runway_time <= now()) AS arr_60,
       COUNT(*) FILTER (WHERE movement = 'DEP' AND runway_time > now() - interval '60 minutes' AND runway_time <= now()) AS dep_60,
       ROUND(AVG(delay_min) FILTER (WHERE movement = 'DEP' AND runway_time > now() - interval '60 minutes'), 1) AS dep_delay_60,
       ROUND(AVG(delay_min) FILTER (WHERE movement = 'ARR' AND runway_time > now() - interval '60 minutes'), 1) AS arr_delay_60,
       COUNT(*) FILTER (WHERE NOT completed AND delay_min >= {config.ON_TIME_MINUTES} AND status <> 'Cancelled'
                        AND sched < now() + interval '3 hours') AS delayed_flights,
       ROUND(100.0 * COUNT(*) FILTER (WHERE completed AND on_time AND sched > now() - interval '24 hours')
             / NULLIF(COUNT(*) FILTER (WHERE completed AND sched > now() - interval '24 hours'), 0), 1) AS otp_24h_pct
       FROM ops_flights)
SELECT r.mov AS movements_last_60, f.arr_60 AS arrivals_last_60, f.dep_60 AS departures_last_60,
       r.planned AS planned_next_60, r.cap AS airport_capacity_per_hour,
       ROUND(100.0 * r.mov / NULLIF(r.cap, 0), 1) AS utilisation_pct,
       {BUSY_CASE.format(x="100.0 * r.mov / NULLIF(r.cap, 0)")} AS busy_level,
       f.dep_delay_60 AS avg_dep_delay_last_60, f.arr_delay_60 AS avg_arr_delay_last_60,
       f.delayed_flights, f.otp_24h_pct,
       w.category AS flight_category, w.vis_km, w.ceiling_ft, w.wind_dir, w.wind_kt, w.gust_kt,
       w.headwind_kt, w.crosswind_kt, w.crosswind_alert, w.qnh_hpa, w.capacity_factor,
       (SELECT string_agg(active_direction, ' / ' ORDER BY runway) FROM v_ops_runways) AS runways_in_use,
       w.raw_metar, to_char(w.obs_time AT TIME ZONE 'Asia/Kolkata', 'HH24:MI') AS metar_time
FROM r, f, w;

CREATE OR REPLACE VIEW v_ops_hourly AS
WITH h AS (
  SELECT generate_series(date_trunc('hour', now() AT TIME ZONE 'Asia/Kolkata') - interval '23 hours',
                         date_trunc('hour', now() AT TIME ZONE 'Asia/Kolkata') + interval '6 hours',
                         interval '1 hour') AS hour_ist)
SELECT h.hour_ist,
       (SELECT COUNT(*) FROM ops_flights f WHERE f.status <> 'Cancelled'
          AND date_trunc('hour', f.sched AT TIME ZONE 'Asia/Kolkata') = h.hour_ist) AS scheduled,
       CASE WHEN h.hour_ist <= now() AT TIME ZONE 'Asia/Kolkata' THEN
         (SELECT COUNT(*) FROM ops_flights f
           WHERE date_trunc('hour', f.runway_time AT TIME ZONE 'Asia/Kolkata') = h.hour_ist) END AS actual,
       CASE WHEN h.hour_ist <= now() AT TIME ZONE 'Asia/Kolkata' THEN
         (SELECT COUNT(*) FROM ops_flights f WHERE f.movement = 'ARR'
           AND date_trunc('hour', f.runway_time AT TIME ZONE 'Asia/Kolkata') = h.hour_ist) END AS arrivals,
       CASE WHEN h.hour_ist <= now() AT TIME ZONE 'Asia/Kolkata' THEN
         (SELECT COUNT(*) FROM ops_flights f WHERE f.movement = 'DEP'
           AND date_trunc('hour', f.runway_time AT TIME ZONE 'Asia/Kolkata') = h.hour_ist) END AS departures,
       (SELECT ROUND(AVG(delay_min), 1) FROM ops_flights f WHERE f.movement = 'DEP' AND f.completed
          AND date_trunc('hour', f.sched AT TIME ZONE 'Asia/Kolkata') = h.hour_ist) AS avg_dep_delay,
       (SELECT ROUND(AVG(delay_min), 1) FROM ops_flights f WHERE f.movement = 'ARR' AND f.completed
          AND date_trunc('hour', f.sched AT TIME ZONE 'Asia/Kolkata') = h.hour_ist) AS avg_arr_delay,
       (SELECT COUNT(*) FROM runways) * COALESCE(
          (SELECT MIN(capacity_per_runway) FROM ops_weather w
            WHERE date_trunc('hour', w.obs_time AT TIME ZONE 'Asia/Kolkata') = h.hour_ist),
          (SELECT capacity_per_runway FROM v_ops_weather_now)) AS capacity
FROM h;

CREATE OR REPLACE VIEW v_ops_upcoming AS
SELECT to_char(COALESCE(est, sched) AT TIME ZONE 'Asia/Kolkata', 'HH24:MI') AS expected,
       flight_iata AS flight, movement, other_iata AS from_to, aircraft, runway, status, delay_min,
       COALESCE(est, sched) AS expected_at
FROM ops_flights
WHERE NOT completed AND status <> 'Cancelled'
  AND COALESCE(est, sched) BETWEEN now() - interval '30 minutes' AND now() + interval '2 hours';

CREATE OR REPLACE VIEW v_ops_delay_buckets AS
SELECT delay_bucket, COUNT(*) AS flights
FROM ops_flights
WHERE sched BETWEEN now() - interval '24 hours' AND now() AND (completed OR status = 'Cancelled')
GROUP BY delay_bucket;

CREATE OR REPLACE VIEW v_ops_airlines AS
SELECT airline_name AS airline,
       COUNT(*) FILTER (WHERE completed) AS flights_operated,
       ROUND(100.0 * COUNT(*) FILTER (WHERE completed AND on_time)
             / NULLIF(COUNT(*) FILTER (WHERE completed), 0), 1) AS on_time_pct,
       ROUND(AVG(delay_min) FILTER (WHERE completed), 1) AS avg_delay_min,
       COUNT(*) FILTER (WHERE status = 'Cancelled') AS cancelled
FROM ops_flights
WHERE sched BETWEEN now() - interval '24 hours' AND now()
GROUP BY airline_name;

CREATE OR REPLACE VIEW v_ops_weather_history AS
SELECT obs_time AT TIME ZONE 'Asia/Kolkata' AS obs_ist, vis_km, wind_kt, COALESCE(gust_kt, wind_kt) AS gust_kt,
       headwind_kt, crosswind_kt, ceiling_ft, temp_c, dew_c, qnh_hpa, category, wx,
       capacity_per_runway * (SELECT COUNT(*) FROM runways) AS airport_capacity, raw_metar
FROM ops_weather
WHERE obs_time > now() - interval '24 hours';

-- ---------------------------------------------------------------- live map
-- Aircraft in the latest snapshot, with the flight's other city from the public board.
CREATE OR REPLACE VIEW v_positions_now AS
WITH latest AS (SELECT MAX(seen_at) AS t FROM aircraft_positions)
SELECT p.hex, p.callsign, p.flight_iata, p.aircraft, p.lat, p.lon, p.alt_ft, p.gs_kt, p.track,
       p.vrate_fpm, p.dist_km, p.bearing, p.phase, p.seen_at,
       f.city, f.status,
       CASE WHEN p.phase = 'Arriving' AND p.gs_kt > 0
            THEN ROUND((p.dist_km / (p.gs_kt * 1.852 / 60))::numeric) END AS minutes_to_land
FROM aircraft_positions p
CROSS JOIN latest
LEFT JOIN LATERAL (
    SELECT pf.city, pf.status FROM public_flights pf
    WHERE pf.flight_iata = p.flight_iata
    ORDER BY ABS(EXTRACT(EPOCH FROM (COALESCE(pf.actual, pf.est, pf.sched) - p.seen_at)))
    LIMIT 1) f ON true
WHERE p.seen_at >= latest.t - interval '30 seconds';

CREATE OR REPLACE VIEW v_position_trails AS
SELECT h.hex, h.seen_at, h.lat, h.lon, h.alt_ft
FROM position_history h
JOIN v_positions_now n ON n.hex = h.hex
WHERE h.seen_at > n.seen_at - interval '5 minutes';

CREATE OR REPLACE VIEW v_positions_summary AS
SELECT COUNT(*) FILTER (WHERE phase <> 'On ground') AS airborne,
       COUNT(*) FILTER (WHERE phase = 'Arriving')   AS arriving,
       COUNT(*) FILTER (WHERE phase = 'Departing')  AS departing,
       COUNT(*) FILTER (WHERE phase = 'Overflying') AS overflying,
       MAX(seen_at) AS snapshot_time
FROM v_positions_now;

-- ---------------------------------------------------------------- Flink results
-- Rolling 60-minute movements per runway, every 5 minutes, for the 12 hours up to the
-- latest movement Flink has counted (= now while the stream is running).
CREATE OR REPLACE VIEW v_flink_runway_rolling AS
WITH ref AS (SELECT MAX(bucket_start) + interval '5 minutes' AS t1 FROM flink_runway_5min),
     steps AS (SELECT generate_series(t1 - interval '12 hours', t1, interval '5 minutes') AS step FROM ref),
     roll AS (
       SELECT s.step, r.name AS runway,
              COALESCE((SELECT SUM(f.movements) FROM flink_runway_5min f
                        WHERE f.runway = r.name AND f.bucket_start >= s.step - interval '60 minutes'
                          AND f.bucket_start < s.step), 0) AS movements_60,
              (SELECT w.capacity_per_runway FROM ops_weather w
                WHERE w.obs_time AT TIME ZONE 'Asia/Kolkata' <= s.step
                ORDER BY w.obs_time DESC LIMIT 1) AS capacity_per_hour
       FROM steps s CROSS JOIN runways r)
SELECT step AS step_ist, runway, movements_60, capacity_per_hour,
       ROUND(100.0 * movements_60 / NULLIF(capacity_per_hour, 0), 1) AS utilisation_pct
FROM roll;

CREATE OR REPLACE VIEW v_flink_now AS
SELECT to_char(MAX(step_ist), 'DD Mon HH24:MI') AS as_of,
       SUM(movements_60) AS movements_60, SUM(capacity_per_hour) AS capacity_per_hour,
       ROUND(100.0 * SUM(movements_60) / NULLIF(SUM(capacity_per_hour), 0), 1) AS utilisation_pct
FROM v_flink_runway_rolling
WHERE step_ist = (SELECT MAX(step_ist) FROM v_flink_runway_rolling);

CREATE OR REPLACE VIEW v_flink_hourly AS
SELECT hour_start AS hour_ist, movement, movements, avg_delay_min, late_flights
FROM flink_hourly
WHERE hour_start > (SELECT MAX(hour_start) FROM flink_hourly) - interval '24 hours';

-- Check: movements per hour counted by Flink vs counted in SQL from ops_flights.
CREATE OR REPLACE VIEW v_flink_check AS
WITH f AS (SELECT hour_start, SUM(movements) AS flink FROM flink_hourly GROUP BY hour_start),
     q AS (SELECT date_trunc('hour', runway_time AT TIME ZONE 'Asia/Kolkata') AS hour_start, COUNT(*) AS sql_count
           FROM ops_flights WHERE runway_time IS NOT NULL GROUP BY 1)
SELECT to_char(COALESCE(f.hour_start, q.hour_start), 'DD Mon HH24:00') AS hour,
       COALESCE(f.hour_start, q.hour_start) AS hour_ist,
       COALESCE(f.flink, 0) AS flink_movements, COALESCE(q.sql_count, 0) AS sql_movements,
       COALESCE(f.flink, 0) - COALESCE(q.sql_count, 0) AS difference
FROM f FULL JOIN q ON q.hour_start = f.hour_start
WHERE COALESCE(f.hour_start, q.hour_start) > (SELECT MAX(hour_start) FROM flink_hourly) - interval '24 hours';

-- ---------------------------------------------------------------- pipeline
CREATE OR REPLACE VIEW v_pipeline_health AS
SELECT consumer, topic, source, COUNT(*) AS events,
       ROUND(AVG(EXTRACT(EPOCH FROM (processed_at - emitted_at)))::numeric, 2) AS avg_lag_s,
       ROUND(MAX(EXTRACT(EPOCH FROM (processed_at - emitted_at)))::numeric, 2) AS max_lag_s,
       ROUND(AVG(validate_ms + transform_ms + store_ms)::numeric, 2) AS avg_processing_ms,
       to_char(MAX(processed_at) AT TIME ZONE 'Asia/Kolkata', 'DD Mon HH24:MI:SS') AS last_event,
       (SELECT COUNT(*) FROM dlq_events d WHERE d.consumer = p.consumer AND d.topic = p.topic) AS dead_letters
FROM pipeline_events p
GROUP BY consumer, topic, source;
"""

VIEW_NAMES = ["v_public_board", "v_public_arrivals", "v_public_departures", "v_public_summary",
              "v_public_status_next3h", "v_public_weather_now", "v_ops_weather_now", "v_ops_runways",
              "v_ops_now", "v_ops_hourly", "v_ops_upcoming", "v_ops_delay_buckets",
              "v_ops_airlines", "v_ops_weather_history", "v_pipeline_health",
              "v_flink_runway_rolling", "v_flink_now", "v_flink_hourly", "v_flink_check",
              "v_positions_now", "v_position_trails", "v_positions_summary"]

READ_ONLY_ROLE = f"""
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dashboard_ro') THEN
    CREATE ROLE dashboard_ro LOGIN PASSWORD 'dashboard_ro';
  END IF;
END
$$;
GRANT USAGE ON SCHEMA public TO dashboard_ro;
GRANT SELECT ON {", ".join(VIEW_NAMES)} TO dashboard_ro;
"""


def main():
    with psycopg.connect(config.PG_DSN) as conn:
        conn.execute(TABLES)
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO runways (name, end_09, end_27, use) VALUES (%s, %s, %s, %s)
                   ON CONFLICT (name) DO UPDATE SET use = EXCLUDED.use""",
                [(r["name"], *r["name"].split("/"), r["use"]) for r in config.RUNWAYS])
        print("tables ready: runways, public_flights, public_weather, ops_flights, ops_weather,")
        print("              aircraft_positions, position_history, flink_runway_5min, flink_hourly,")
        print("              pipeline_events, dlq_events")
        conn.execute(VIEWS)
        print(f"views ready:  {len(VIEW_NAMES)} views (v_public_*, v_ops_*, v_positions_*, v_flink_*, v_pipeline_health)")
        conn.execute(READ_ONLY_ROLE)
        conn.execute(f'GRANT CONNECT ON DATABASE "{conn.info.dbname}" TO dashboard_ro')
        print("read-only role dashboard_ro (used by the web dashboard) can read the views")


if __name__ == "__main__":
    main()
