"""Producer 3: live aircraft positions around BLR -> Kafka topic positions.raw.

    python positions_producer.py            # one snapshot
    python positions_producer.py --loop     # a snapshot every POSITIONS_POLL_SECONDS (10 s)
    python positions_producer.py --offline  # skip adsb.lol, use the position simulator

Positions come from adsb.lol (community ADS-B receivers, no API key): every aircraft
within POSITIONS_RADIUS_NM of the airport. Each message is one snapshot of all of them.
If adsb.lol cannot be reached, offline_data.positions_at() places the simulated
arriving and departing flights on their approach and climb-out paths.
"""
import argparse
import json
import logging
import time
import uuid
from datetime import datetime, timezone

import requests

import config
from flight_producer import create_topics, make_producer
from offline_data import positions_at

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("producer.positions")


def fetch_live():
    url = config.ADSB_URL.format(lat=config.AIRPORT["lat"], lon=config.AIRPORT["lon"],
                                 radius=config.POSITIONS_RADIUS_NM)
    res = requests.get(url, timeout=config.API_TIMEOUT)
    res.raise_for_status()
    body = res.json()
    # keep only aircraft that report a position
    return [a for a in body.get("ac", []) if "lat" in a and "lon" in a]


def publish(producer, use_api):
    now = datetime.now(timezone.utc)
    source = "offline"
    if use_api:
        try:
            aircraft, source = fetch_live(), "live"
        except (requests.RequestException, ValueError) as e:
            log.warning("adsb.lol fetch failed (%s); using position simulator", type(e).__name__)
    if source == "offline":
        aircraft = positions_at(now)
    event = {
        "event_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"BLR-positions/{now.isoformat()}")),
        "emitted_at": now.isoformat(),
        "snapshot_time": now.isoformat(),
        "source": source,
        "aircraft": aircraft,
    }
    producer.produce(config.TOPIC_POSITIONS, key=b"BLR", value=json.dumps(event).encode())
    if producer.flush(10):
        raise RuntimeError("snapshot was not delivered")
    log.info("snapshot (%s): %d aircraft near %s", source, len(aircraft), config.AIRPORT["iata"])
    return source == "live"


def main():
    parser = argparse.ArgumentParser(description="Publish aircraft positions around BLR to Kafka")
    parser.add_argument("--loop", action="store_true", help="repeat every POSITIONS_POLL_SECONDS")
    parser.add_argument("--offline", action="store_true", help="use the position simulator only")
    args = parser.parse_args()

    create_topics()
    producer = make_producer()
    failures = 0
    try:
        while True:
            # After a failed fetch, try adsb.lol again only every 30th snapshot (5 minutes).
            use_api = not args.offline and failures % 30 == 0
            live = publish(producer, use_api)
            failures = 0 if live else failures + 1
            if not args.loop:
                break
            time.sleep(config.POSITIONS_POLL_SECONDS)
    except KeyboardInterrupt:
        log.info("stopped")


if __name__ == "__main__":
    main()
