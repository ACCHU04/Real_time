"""Producer 1: flight arrivals and departures at BLR -> Kafka topic flights.raw.

    python flight_producer.py            # publish once
    python flight_producer.py --loop     # keep polling every FLIGHT_POLL_SECONDS
    python flight_producer.py --offline  # skip the API, use the schedule simulator

Each message is one flight status event. On the first cycle every flight in the
window (24 h back, 12 h ahead) is sent; after that only flights whose status, times
or gate changed. If AVIATIONSTACK_KEY is not set or the API cannot be reached,
the offline simulator in offline_data.py is used and messages are marked "offline".
"""
import argparse
import hashlib
import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone

import requests
from confluent_kafka import KafkaException, Producer
from confluent_kafka.admin import AdminClient, NewTopic

import config
from airport_transform import IST
from offline_data import flight_state, schedule_for

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("producer.flights")


# ---------------------------------------------------------------------------
# 1. Kafka setup
# ---------------------------------------------------------------------------
def create_topics():
    admin = AdminClient({"bootstrap.servers": config.KAFKA_BROKER})
    existing = admin.list_topics(timeout=10).topics
    new = [NewTopic(t, num_partitions=3, replication_factor=1)
           for t in (config.TOPIC_FLIGHTS, config.TOPIC_WEATHER, config.TOPIC_POSITIONS, config.TOPIC_DLQ)
           if t not in existing]
    for topic, future in (admin.create_topics(new).items() if new else []):
        try:
            future.result()
            log.info("created topic %s", topic)
        except KafkaException as e:
            if "TOPIC_ALREADY_EXISTS" not in str(e):
                raise


def make_producer():
    return Producer({
        "bootstrap.servers": config.KAFKA_BROKER,
        "enable.idempotence": True,
        "acks": "all",
        "linger.ms": 20,
    })


# ---------------------------------------------------------------------------
# 2. Live data from AviationStack
# ---------------------------------------------------------------------------
DEP_STATUS = {"scheduled": "Scheduled", "active": "Departed", "landed": "Departed",
              "cancelled": "Cancelled", "incident": "Delayed", "diverted": "Departed"}
ARR_STATUS = {"scheduled": "Scheduled", "active": "En Route", "landed": "Arrived",
              "cancelled": "Cancelled", "incident": "Delayed", "diverted": "Diverted"}


def local(value):
    """AviationStack returns airport local time with a '+00:00' suffix; treat it as IST."""
    return value[:19] + "+05:30" if value else None


def from_aviationstack(item, movement):
    side = item["departure"] if movement == "DEP" else item["arrival"]
    status = (DEP_STATUS if movement == "DEP" else ARR_STATUS).get(item.get("flight_status"), "Scheduled")
    if status == "Scheduled" and (side.get("delay") or 0) >= config.ON_TIME_MINUTES:
        status = "Delayed"
    number = item["flight"].get("iata") or item["flight"].get("number")
    return {
        "flight_id": f"{item.get('flight_date', '').replace('-', '')}-{number}-{movement}",
        "flight_iata": number,
        "airline_iata": item["airline"].get("iata"),
        "airline_name": item["airline"].get("name"),
        "movement": movement,
        "origin_iata": item["departure"].get("iata"), "origin_city": item["departure"].get("airport"),
        "dest_iata": item["arrival"].get("iata"), "dest_city": item["arrival"].get("airport"),
        "aircraft": (item.get("aircraft") or {}).get("iata"),
        "terminal": side.get("terminal"), "gate": side.get("gate"), "runway": None,
        "status": status,
        "sched_time": local(side.get("scheduled")),
        "est_time": local(side.get("estimated")),
        "actual_time": local(side.get("actual")),
        "runway_time": local(side.get("actual_runway")),
    }


def fetch_live():
    flights = []
    for movement, param in (("DEP", "dep_iata"), ("ARR", "arr_iata")):
        res = requests.get(config.AVIATIONSTACK_URL, timeout=config.API_TIMEOUT, params={
            "access_key": config.AVIATIONSTACK_KEY, param: config.AIRPORT["iata"], "limit": 100})
        res.raise_for_status()
        body = res.json()
        if "error" in body:
            raise ValueError(body["error"].get("message", "API error"))
        flights += [from_aviationstack(i, movement) for i in body["data"]]
    return flights


# ---------------------------------------------------------------------------
# 3. Offline schedule simulator
# ---------------------------------------------------------------------------
def iso(t):
    return t.isoformat() if t else None


def fetch_offline(now):
    start = now - timedelta(hours=config.WINDOW_PAST_HOURS)
    end = now + timedelta(hours=config.WINDOW_AHEAD_HOURS)
    flights = []
    day = start.astimezone(IST).date()
    while day <= end.astimezone(IST).date():
        for f in schedule_for(day):
            if start <= f["sched"] <= end:
                s = flight_state(f, now)
                flights.append({
                    **{k: f[k] for k in ("flight_id", "flight_iata", "airline_iata", "airline_name",
                                         "movement", "origin_iata", "origin_city", "dest_iata",
                                         "dest_city", "aircraft", "terminal", "gate")},
                    "runway": s["runway"], "status": s["status"],
                    "sched_time": iso(f["sched"]), "est_time": iso(s["est_time"]),
                    "actual_time": iso(s["actual_time"]), "runway_time": iso(s["runway_time"]),
                })
        day += timedelta(days=1)
    return flights


# ---------------------------------------------------------------------------
# 4. Publish only what changed
# ---------------------------------------------------------------------------
def fingerprint(f):
    return hashlib.sha1(json.dumps(f, sort_keys=True).encode()).hexdigest()


def publish_cycle(producer, last_sent, offline=False):
    now = datetime.now(timezone.utc)
    source = "offline"
    if not offline and config.AVIATIONSTACK_KEY:
        try:
            flights, source = fetch_live(), "live"
        except (requests.RequestException, KeyError, ValueError) as e:
            log.warning("live fetch failed (%s); using offline simulator", type(e).__name__)
    if source == "offline":
        flights = fetch_offline(now)

    sent = 0
    for f in flights:
        fp = fingerprint(f)
        if last_sent.get(f["flight_id"]) == fp:
            continue
        event = {
            # Same flight in the same state -> same event_id, so a resend is harmless.
            "event_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{f['flight_id']}/{fp}")),
            "emitted_at": datetime.now(timezone.utc).isoformat(),
            "source": source,
            "flight": f,
        }
        producer.produce(config.TOPIC_FLIGHTS, key=f["flight_id"].encode(), value=json.dumps(event).encode())
        producer.poll(0)
        last_sent[f["flight_id"]] = fp
        sent += 1
    if producer.flush(30):
        raise RuntimeError("some messages were not delivered")
    by_status = {}
    for f in flights:
        by_status[f["status"]] = by_status.get(f["status"], 0) + 1
    log.info("cycle done (%s): %d flights in window, %d events published", source, len(flights), sent)
    log.info("status now: %s", ", ".join(f"{k} {v}" for k, v in sorted(by_status.items())))
    return sent


def main():
    parser = argparse.ArgumentParser(description="Publish BLR flight status events to Kafka")
    parser.add_argument("--loop", action="store_true", help="repeat every FLIGHT_POLL_SECONDS")
    parser.add_argument("--offline", action="store_true", help="use the offline simulator only")
    args = parser.parse_args()

    create_topics()
    producer = make_producer()
    last_sent = {}
    try:
        while True:
            publish_cycle(producer, last_sent, args.offline)
            if not args.loop:
                break
            time.sleep(config.FLIGHT_POLL_SECONDS)
    except KeyboardInterrupt:
        log.info("stopped")


if __name__ == "__main__":
    main()
