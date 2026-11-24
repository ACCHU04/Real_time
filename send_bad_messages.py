"""Dead-letter queue test: send one bad flight event and one bad METAR.

    python send_bad_messages.py

Both consumers should reject them and forward them to airport.dlq (see read_dlq.py).
"""
import json
import uuid
from datetime import datetime, timezone

import config
from flight_producer import make_producer

now = datetime.now(timezone.utc).isoformat()
flight = {"event_id": str(uuid.uuid4()), "emitted_at": now, "source": "offline", "flight": {
    "flight_id": "20261124-6E999-DEP", "flight_iata": "6E999", "airline_iata": "6E", "airline_name": "IndiGo",
    "movement": "DEP", "origin_iata": "BLR", "dest_iata": "DEL", "status": "Landed",   # not a departure status
    "sched_time": "2026-11-24T02:00:00+05:30"}}
metar = {"event_id": str(uuid.uuid4()), "emitted_at": now, "source": "offline", "metar": {
    "icaoId": "VOBL", "reportTime": "2026-11-23T20:15:00Z", "temp": 18, "dewp": 24,   # dew point above temperature
    "wdir": 90, "wspd": 6, "visib": "6+", "altim": 1017, "clouds": [],
    "rawOb": "METAR VOBL 232015Z 09006KT 9999 18/24 Q1017"}}

p = make_producer()
p.produce(config.TOPIC_FLIGHTS, key=flight["flight"]["flight_id"].encode(), value=json.dumps(flight).encode())
p.produce(config.TOPIC_WEATHER, key=b"VOBL", value=json.dumps(metar).encode())
p.flush(10)
print("sent 1 bad flight event (DEP with status 'Landed') and 1 bad METAR (dew point above temperature)")
