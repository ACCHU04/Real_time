"""The Kafka loop shared by both consumers.

Each consumer passes its own group id and two handlers (one for flights, one for
weather). For every message the loop: validates, transforms, stores in one
database transaction, logs timings, and commits the Kafka offset only after the
write succeeded (at-least-once). Bad messages go to the dead-letter topic.
"""
import json
import logging
import time
from datetime import datetime

import psycopg
from confluent_kafka import Consumer, KafkaError, Producer

import config
from airport_transform import (ValidationError, transform_flight, transform_metar, transform_positions,
                               validate_flight, validate_metar, validate_positions)

STEPS = {
    config.TOPIC_FLIGHTS: (validate_flight, transform_flight),
    config.TOPIC_WEATHER: (validate_metar, transform_metar),
    config.TOPIC_POSITIONS: (validate_positions, transform_positions),
}


def log_event(conn, consumer, topic, event, timings):
    conn.execute(
        """INSERT INTO pipeline_events (event_id, consumer, topic, source, emitted_at,
                                        validate_ms, transform_ms, store_ms)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (event_id, consumer) DO UPDATE
             SET processed_at = now(), store_ms = EXCLUDED.store_ms""",
        (event["event_id"], consumer, topic, event["source"],
         datetime.fromisoformat(event["emitted_at"]),
         timings["validate_ms"], timings["transform_ms"], timings["store_ms"]))


def send_to_dlq(conn, dlq, consumer, kafka_msg, error):
    dlq.produce(config.TOPIC_DLQ, key=kafka_msg.key(), value=json.dumps({
        "error": error,
        "consumer": consumer,
        "source": {"topic": kafka_msg.topic(), "partition": kafka_msg.partition(),
                   "offset": kafka_msg.offset()},
        "original": kafka_msg.value().decode("utf-8", errors="replace")[:100_000],
    }).encode())
    dlq.flush(10)
    conn.execute("INSERT INTO dlq_events (consumer, topic, kafka_offset, error) VALUES (%s, %s, %s, %s)",
                 (consumer, kafka_msg.topic(), kafka_msg.offset(), error[:500]))
    conn.commit()


def run(consumer_name, group_id, handlers, max_messages=None, quiet_after=None):
    """handlers: {topic: (save(conn, event, model), describe(model) -> str)}"""
    log = logging.getLogger(f"consumer.{consumer_name}")
    consumer = Consumer({
        "bootstrap.servers": config.KAFKA_BROKER,
        "group.id": group_id,
        "auto.offset.reset": "earliest",
        "enable.auto.commit": False,     # commit only after the database write succeeds
    })
    dlq = Producer({"bootstrap.servers": config.KAFKA_BROKER})
    conn = psycopg.connect(config.PG_DSN)
    topics = list(handlers)
    consumer.subscribe(topics)
    log.info("listening on %s as group %s", ", ".join(topics), group_id)

    done, idle_since = 0, time.time()
    try:
        while max_messages is None or done < max_messages:
            kafka_msg = consumer.poll(1.0)
            if kafka_msg is None:
                if quiet_after and time.time() - idle_since > quiet_after:
                    break                    # used for test runs: stop when the topic is drained
                continue
            if kafka_msg.error():
                if kafka_msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                raise RuntimeError(kafka_msg.error())
            idle_since = time.time()

            topic = kafka_msg.topic()
            validate, transform = STEPS[topic]
            save, describe = handlers[topic]
            try:
                event = json.loads(kafka_msg.value())
                t0 = time.perf_counter()
                validate(event)
                t1 = time.perf_counter()
                model = transform(event)
                t2 = time.perf_counter()
                with conn.transaction():
                    save(conn, event, model)
                    timings = {"validate_ms": round((t1 - t0) * 1000, 2),
                               "transform_ms": round((t2 - t1) * 1000, 2),
                               "store_ms": round((time.perf_counter() - t2) * 1000, 2)}
                    log_event(conn, consumer_name, topic, event, timings)
                line = describe(model)
                if line:
                    log.info("%s", line)
            except (json.JSONDecodeError, ValidationError, KeyError, TypeError, ValueError) as e:
                log.error("bad message on %s offset %d: %s -> sent to %s",
                          topic, kafka_msg.offset(), e, config.TOPIC_DLQ)
                send_to_dlq(conn, dlq, consumer_name, kafka_msg, str(e))

            consumer.commit(message=kafka_msg, asynchronous=False)
            done += 1
    except KeyboardInterrupt:
        pass
    finally:
        consumer.close()
        conn.close()
    log.info("processed %d message(s)", done)
    return done
