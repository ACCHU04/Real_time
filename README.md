# Real-Time Flight & Weather Dashboard – BLR (Assignment 4)

Kafka • three producers • two consumer groups • Apache Flink • PostgreSQL • web dashboard with a live map

## 1. What it does

Flight movements, schedules, live aircraft positions and weather for Kempegowda International Airport,
Bengaluru (BLR / VOBL) are streamed through Kafka into PostgreSQL and shown on a web dashboard with
three live pages:

1. **Public board** (`/`) – arrivals and departures boards (scheduled and expected time, status, gate),
   flights in the next hour, on-time %, delayed and cancelled flights, the weather in plain words with a
   travel advisory, and how many aircraft are in the sky around BLR. Refreshes every 30 s.
2. **Flight operations** (`/ops`) – runway movements in the last hour against capacity (utilisation and a
   busy level: Low / Moderate / High / Saturated), runways in use, aviation weather, hourly demand vs
   capacity, delays by hour and airline, the runway sequence for the next 2 hours, pipeline health, and
   the rolling runway load computed by Apache Flink. Refreshes every 30 s.
3. **Live map** (`/map`) – a Leaflet map on OpenStreetMap tiles showing every aircraft within 60 km
   (arriving, departing or overflying) with flight number, altitude and a 5-minute trail, the two runways
   at their real positions, range rings, and "approaching" and "just departed" tables. Refreshes every 5 s.
   Without internet the tiles cannot load, so the page switches to a built-in base map
   (`static/vendor/blr_basemap.js`: Natural Earth highways, railways and towns, and the Bengaluru city
   limit from DataMeet).

Sources: flights from **AviationStack** (free key, set `AVIATIONSTACK_KEY`), weather from
**aviationweather.gov METAR** (no key), positions from **adsb.lol** (ADS-B, no key). If a source cannot
be reached, the producers use the offline simulator in `offline_data.py`, so the pipeline still runs.

## 2. Architecture

```
AviationStack   ─> flight_producer.py    ─> flights.raw   ─┐   ┌─> consumer_public.py (group public_board_group) ─> public_*, aircraft_positions ─┐
aviationweather ─> weather_producer.py   ─> weather.raw   ─┼───┤                                                                                   ├─> views ─> web_dashboard.py
adsb.lol        ─> positions_producer.py ─> positions.raw ─┘   └─> consumer_ops.py    (group ops_control_group)  ─> ops_*                      ─┤   (/, /ops, /map)
                                     flights.raw ─> flink_runway_job.py (Apache Flink, group flink_runway_group) ─> flink_*                   ─┘
                                     bad messages ─> airport.dlq
```

- Each consumer has its **own consumer group**, so both receive every flight and weather message;
  consumer 1 also stores the live positions for the map.
- Offsets are committed only **after** the database write succeeds (at-least-once). Writes are upserts
  keyed by flight id / observation time / aircraft, and an older event never overwrites a newer one.
- **Apache Flink** reads `flights.raw` as a third consumer group and keeps one row of state per flight,
  so repeated or late events are counted once; it writes runway movements per 5 minutes and per hour
  to PostgreSQL through the JDBC upsert sink.
- Messages that fail validation go to `airport.dlq` (and the `dlq_events` table).
- The web dashboard logs in as the read-only user `dashboard_ro`, which can read only the views.

## 3. Files

| File | Purpose |
|---|---|
| `config.py` | Airport, runways, capacity and metric thresholds; Kafka, PostgreSQL, Flink and dashboard settings |
| `flight_producer.py` | Flight status events (AviationStack or simulator) -> `flights.raw`; sends only changes |
| `weather_producer.py` | METAR reports -> `weather.raw` |
| `positions_producer.py` | Aircraft positions within ~60 km (adsb.lol or simulator) -> `positions.raw`, every 10 s |
| `offline_data.py` | Offline schedule simulator, METAR model and position simulator |
| `airport_transform.py` | Shared validation and transformation (times, delays, units, flight category, wind, capacity, distance/bearing, flight phase) |
| `consumer_common.py` | Kafka loop shared by both consumers: validate, transform, store, commit, DLQ |
| `consumer_public.py` | Consumer 1: public flight board, weather and live positions |
| `consumer_ops.py` | Consumer 2: runway movements, delays, aviation weather |
| `flink_runway_job.py` | Apache Flink job (PyFlink SQL): rolling runway load and hourly delays |
| `setup_airport_db.py` | Tables, 22 views and the read-only dashboard user |
| `web_dashboard.py`, `templates/`, `static/` | Flask web dashboard: three pages and their JSON endpoints. Chart.js, Leaflet, the offline base map and the Barlow font are bundled in `static/`, so nothing is loaded from a CDN except the map tiles |
| `send_bad_messages.py`, `read_dlq.py` | Dead-letter queue test |
| `test_airport.py` | 31 tests (the Flink one runs only where PyFlink and Java are installed) |
| `docker-compose.yml`, `docker/flink/` | PostgreSQL, Kafka and (profile `flink`) a Flink cluster with the Kafka/JDBC connectors |
| `Dashboard_Images/`, `Terminal_Images/` | Screenshots of a test run |

## 4. How to run (Windows)

```powershell
docker compose up -d                  # PostgreSQL and Kafka
pip install -r requirements.txt
$env:AVIATIONSTACK_KEY = "your key"   # optional; without it the flight simulator is used
$env:FLIGHT_POLL_SECONDS = "900"      # optional; keeps within AviationStack's free quota

python setup_airport_db.py            # tables + views
python weather_producer.py --loop     # terminal 1
python flight_producer.py --loop      # terminal 2
python positions_producer.py --loop   # terminal 3
python consumer_public.py             # terminal 4
python consumer_ops.py                # terminal 5
docker compose --profile flink up -d --build   # Flink cluster + flink_runway_job.py (UI: http://localhost:8081)
python web_dashboard.py               # terminal 6, then open http://localhost:5000
pytest -v
```

Add `--offline` to any producer to force the simulator (useful for a demo without internet).

To run the Flink job without Docker instead: install Java 11 or 17, `pip install -r requirements-flink.txt`,
put `flink-sql-connector-kafka-3.3.0-1.20.jar`, `flink-connector-jdbc-3.3.0-1.20.jar` and
`postgresql-42.7.4.jar` (from Maven Central) in a folder `flink_jars/`, then `python flink_runway_job.py`.
`python flink_runway_job.py --source-file events.jsonl --print` runs it on a file and prints the changelog.

## 5. Metrics

| Page | Metric | Definition |
|---|---|---|
| Public | Arrivals / departures next 60 min | not yet landed/departed or cancelled, expected within 60 min |
| Public | On-time % (24 h) | completed flights less than 15 min late / completed flights |
| Public | Delayed now, cancelled | status Delayed; status Cancelled (last 24 h and next 12 h) |
| Public | Weather now | temperature, feels-like, humidity, wind km/h, visibility km, condition, advisory |
| Ops | Runway movements last 60 min | takeoffs + landings with runway time in the last 60 min |
| Ops | Utilisation % and busy level | movements last 60 min / capacity; Low < 50 ≤ Moderate < 75 ≤ High < 90 ≤ Saturated |
| Ops | Capacity | 30 movements per runway per hour × weather factor (VFR 1.0, MVFR 0.9, IFR 0.75, LIFR 0.6, × 0.7 in thunderstorms) |
| Ops | Hourly demand vs capacity | scheduled and actual movements per hour, last 24 h and next 6 h |
| Ops | Delays | average delay by hour, delay bands, airline on-time %, runway sequence next 2 h |
| Ops | Weather | flight category, ceiling, visibility, head/crosswind on the runway in use (alert above 20 kt) |
| Ops (Flink) | Rolling 60-min runway load | movements per runway per 5-min bucket, summed over the last 12 buckets; utilisation vs capacity |
| Ops (Flink) | Hourly movements and late flights | per hour and ARR/DEP, from Flink's per-flight state; checked against SQL in `v_flink_check` |
| Map | Aircraft near BLR | aircraft within 60 km by phase: Arriving (descending / heading in), Departing (climbing), Overflying |
| Map | Approaching / just departed | distance, altitude, minutes to landing (distance / ground speed), climb rate |

The capacity of 30 per runway per hour and the arrivals/departures runway split are planning
assumptions set in `config.py`, not the airport's declared figures.
