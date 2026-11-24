"""Producer 2: METAR weather reports for BLR (VOBL) -> Kafka topic weather.raw.

    python weather_producer.py             # last 24 h of reports, then stop
    python weather_producer.py --loop      # then keep checking every WEATHER_POLL_SECONDS
    python weather_producer.py --offline   # skip the API, use the offline weather model

Reports come from aviationweather.gov (no API key). A report is sent only once. The
station code is the message key, so all reports go to one partition and stay in order;
the consumers also ignore a repeated observation time.
"""
import argparse
import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone

import requests

import config
from flight_producer import create_topics, make_producer
from offline_data import metar_at

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("producer.weather")


def fetch_live(hours):
    res = requests.get(config.METAR_URL, timeout=config.API_TIMEOUT, params={
        "ids": config.AIRPORT["icao"], "format": "json", "hours": hours})
    res.raise_for_status()
    reports = res.json()
    if not reports:
        raise ValueError("no METAR returned")
    for m in reports:                                   # the API gives reportTime with ".000Z"
        m["reportTime"] = m["reportTime"].replace(".000Z", "Z")
    return sorted(reports, key=lambda m: m["reportTime"])


def fetch_offline(hours):
    now = datetime.now(timezone.utc)
    return [metar_at(now - timedelta(minutes=30 * i)) for i in range(hours * 2, -1, -1)]


def publish(producer, sent, hours, offline=False):
    source = "offline"
    if not offline:
        try:
            reports, source = fetch_live(hours), "live"
        except (requests.RequestException, KeyError, ValueError) as e:
            log.warning("METAR fetch failed (%s); using offline weather model", type(e).__name__)
    if source == "offline":
        reports = fetch_offline(hours)

    new = [m for m in reports if m["reportTime"] not in sent]
    for m in new:
        event = {
            "event_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"VOBL/{m['reportTime']}")),
            "emitted_at": datetime.now(timezone.utc).isoformat(),
            "source": source,
            "metar": m,
        }
        producer.produce(config.TOPIC_WEATHER, key=m["icaoId"].encode(), value=json.dumps(event).encode())
        sent.add(m["reportTime"])
    if producer.flush(30):
        raise RuntimeError("some messages were not delivered")
    if new:
        log.info("published %d report(s) (%s); latest: %s", len(new), source, new[-1]["rawOb"])
    else:
        log.info("no new report")


def main():
    parser = argparse.ArgumentParser(description="Publish BLR METAR reports to Kafka")
    parser.add_argument("--loop", action="store_true", help="repeat every WEATHER_POLL_SECONDS")
    parser.add_argument("--offline", action="store_true", help="use the offline weather model only")
    args = parser.parse_args()

    create_topics()
    producer = make_producer()
    sent = set()
    try:
        publish(producer, sent, 24, args.offline)
        while args.loop:
            time.sleep(config.WEATHER_POLL_SECONDS)
            publish(producer, sent, 2, args.offline)
    except KeyboardInterrupt:
        log.info("stopped")


if __name__ == "__main__":
    main()
