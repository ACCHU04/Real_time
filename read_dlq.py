"""Print what is in the dead-letter topic airport.dlq (the original message is left out).

    python read_dlq.py
"""
import json
import uuid

from confluent_kafka import Consumer

import config

c = Consumer({"bootstrap.servers": config.KAFKA_BROKER, "group.id": "dlq-reader-" + uuid.uuid4().hex[:6],
              "auto.offset.reset": "earliest"})
c.subscribe([config.TOPIC_DLQ])
got, empty = 0, 0
while empty < 5:
    m = c.poll(1.0)
    if m is None or m.error():
        empty += 1
        continue
    v = json.loads(m.value())
    v.pop("original")
    print(json.dumps({"key": m.key().decode(), **v}))
    got += 1
c.close()
print(f"{got} message(s) in {config.TOPIC_DLQ}")
