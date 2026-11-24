"""Apache Flink job: runway movements computed inside the stream.

Flink reads flights.raw as a third consumer group (flink_runway_group) and keeps
two results up to date in PostgreSQL while events arrive:

  flink_runway_5min  movements and average delay per runway per 5-minute bucket
                     (the views sum 12 buckets to get the rolling 60-minute load)
  flink_hourly       movements, average delay and late flights per hour, ARR / DEP

A flight sends several events (Taxiing -> Departed, Landed -> Arrived ...). Flink keeps
one row of state per flight, so each runway movement is counted once, even when
events repeat or arrive late; when a flight's row changes, Flink retracts the old
counts and adds the new ones.

    docker compose --profile flink up -d      # Flink cluster + this job (jars included)

    python flink_runway_job.py                # local run: needs Java 11/17 and the
                                              # connector jars in ./flink_jars
    python flink_runway_job.py --source-file events.jsonl --print
                                              # test: read a file, print the changelog
"""
import argparse
import glob
import os

from pyflink.table import EnvironmentSettings, TableEnvironment

import config

# Each movement type uses one runway (config.RUNWAYS, segregated mode).
RUNWAY_CASE = "CASE flight.movement " + " ".join(
    f"WHEN '{r['use']}' THEN '{r['name']}'" for r in config.RUNWAYS) + " END"


def ts(field):
    """'2026-11-24T01:23:00+05:30' -> TIMESTAMP 2026-11-24 01:23:00 (airport local time, IST)."""
    return f"TO_TIMESTAMP(REPLACE(SUBSTRING({field} FROM 1 FOR 19), 'T', ' '))"


SOURCE_COLUMNS = """
    event_id    STRING,
    emitted_at  STRING,
    source      STRING,
    flight ROW<flight_id STRING, flight_iata STRING, movement STRING, runway STRING,
               status STRING, sched_time STRING, est_time STRING, actual_time STRING,
               runway_time STRING>
"""


def kafka_source():
    return f"""
    CREATE TABLE flight_events ({SOURCE_COLUMNS}) WITH (
        'connector' = 'kafka',
        'topic' = '{config.TOPIC_FLIGHTS}',
        'properties.bootstrap.servers' = '{config.FLINK_KAFKA_BROKER}',
        'properties.group.id' = '{config.GROUP_FLINK}',
        'scan.startup.mode' = 'earliest-offset',
        'format' = 'json',
        'json.ignore-parse-errors' = 'true'
    )"""


def file_source(path):
    return f"""
    CREATE TABLE flight_events ({SOURCE_COLUMNS}) WITH (
        'connector' = 'filesystem',
        'path' = '{os.path.abspath(path)}',
        'format' = 'json',
        'json.ignore-parse-errors' = 'true'
    )"""


def sink(name, columns, key, printing):
    if printing:
        options = f"'connector' = 'print', 'print-identifier' = '{name}'"
    else:
        options = (f"'connector' = 'jdbc', 'url' = '{config.FLINK_JDBC_URL}', 'table-name' = '{name}', "
                   f"'username' = '{config.FLINK_JDBC_USER}', 'password' = '{config.FLINK_JDBC_PASSWORD}'")
    return f"CREATE TABLE {name} ({columns}, PRIMARY KEY ({key}) NOT ENFORCED) WITH ({options})"


# 1. Runway movements only: a runway time is set once the flight has taken off or landed.
RUNWAY_EVENTS = f"""
CREATE TEMPORARY VIEW runway_events AS
SELECT flight.flight_id AS flight_id,
       flight.movement  AS movement,
       CAST({RUNWAY_CASE} AS STRING) AS runway,
       {ts('flight.runway_time')} AS runway_ts,
       TIMESTAMPDIFF(MINUTE, {ts('flight.sched_time')},
                     {ts('COALESCE(flight.actual_time, flight.est_time, flight.sched_time)')}) AS delay_min
FROM flight_events
WHERE flight.runway_time IS NOT NULL
  AND flight.status IN ('Departed', 'Landed', 'Arrived')
"""

# 2. One row per flight (keyed state): repeats are absorbed and the latest delay wins.
#    Movement, runway and runway time do not change once set, so MAX simply keeps them.
MOVEMENTS = """
CREATE TEMPORARY VIEW movements AS
SELECT flight_id,
       MAX(movement)         AS movement,
       MAX(runway)           AS runway,
       MAX(runway_ts)        AS runway_ts,
       LAST_VALUE(delay_min) AS delay_min
FROM runway_events
GROUP BY flight_id
"""

# 3a. Per runway, per 5-minute bucket.
RUNWAY_5MIN = """
INSERT INTO flink_runway_5min
SELECT runway, bucket_start, COUNT(*) AS movements,
       ROUND(AVG(CAST(delay_min AS DOUBLE)), 1) AS avg_delay_min
FROM (SELECT runway, delay_min,
             TIMESTAMPADD(MINUTE, -MOD(MINUTE(runway_ts), 5), FLOOR(runway_ts TO MINUTE)) AS bucket_start
      FROM movements)
GROUP BY runway, bucket_start
"""

# 3b. Per hour, arrivals and departures.
HOURLY = f"""
INSERT INTO flink_hourly
SELECT FLOOR(runway_ts TO HOUR) AS hour_start, movement, COUNT(*) AS movements,
       ROUND(AVG(CAST(delay_min AS DOUBLE)), 1) AS avg_delay_min,
       SUM(CASE WHEN delay_min >= {config.ON_TIME_MINUTES} THEN 1 ELSE 0 END) AS late_flights
FROM movements
GROUP BY FLOOR(runway_ts TO HOUR), movement
"""


def main():
    parser = argparse.ArgumentParser(description="Flink job: runway movements per 5 min and per hour")
    parser.add_argument("--source-file", help="read flight events from a JSON-lines file instead of Kafka")
    parser.add_argument("--print", action="store_true", help="print the changelog instead of writing to PostgreSQL")
    args = parser.parse_args()

    t_env = TableEnvironment.create(EnvironmentSettings.in_streaming_mode())
    conf = t_env.get_config()
    conf.set("parallelism.default", "1")
    conf.set("pipeline.name", "BLR runway movements")
    jars = sorted(glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), "flink_jars", "*.jar")))
    if jars:                                          # local run; in docker the jars are in /opt/flink/lib
        conf.set("pipeline.jars", ";".join("file://" + j for j in jars))

    t_env.execute_sql(file_source(args.source_file) if args.source_file else kafka_source())
    t_env.execute_sql(sink("flink_runway_5min",
                           "runway STRING, bucket_start TIMESTAMP(3), movements BIGINT, avg_delay_min DOUBLE",
                           "runway, bucket_start", args.print))
    t_env.execute_sql(sink("flink_hourly",
                           "hour_start TIMESTAMP(3), movement STRING, movements BIGINT, avg_delay_min DOUBLE, "
                           "late_flights INT", "hour_start, movement", args.print))
    t_env.execute_sql(RUNWAY_EVENTS)
    t_env.execute_sql(MOVEMENTS)

    statements = t_env.create_statement_set()
    statements.add_insert_sql(RUNWAY_5MIN)
    statements.add_insert_sql(HOURLY)
    print(f"Flink job started: {'file ' + args.source_file if args.source_file else 'Kafka ' + config.TOPIC_FLIGHTS}"
          f" -> {'stdout' if args.print else 'PostgreSQL'}", flush=True)
    statements.execute().wait()      # runs until cancelled (Kafka) or the file is read (test)


if __name__ == "__main__":
    main()
