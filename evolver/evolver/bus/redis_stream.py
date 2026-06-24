"""Redis Streams consumer — the TS↔Python boundary.

Valor (TS) does XADD valor.signals '*' payload=<json>; this consumer reads with a
consumer group (durable, replayable, at-least-once) and runs the inner loop.
Swap for SQS in cloud with the same shape.

Run: python -m evolver.bus.redis_stream
"""
from __future__ import annotations

import json
import os

STREAM = os.getenv("SIGNAL_STREAM", "valor.signals")
GROUP = os.getenv("SIGNAL_GROUP", "evolver")
CONSUMER = os.getenv("HOSTNAME", "evolver-1")


def consume() -> None:
    import time
    import redis  # declared dep
    from redis.exceptions import RedisError
    from evolver.loop import run_inner

    r = redis.Redis.from_url(
        os.getenv("REDIS_URL", "redis://localhost:6379"),
        socket_timeout=None,        # blocking XREADGROUP must not time out client-side
        socket_keepalive=True,
        health_check_interval=30,
    )
    try:
        r.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
    except redis.ResponseError:
        pass  # group already exists

    print(f"[bus] consuming {STREAM} as {GROUP}/{CONSUMER}", flush=True)
    while True:
        try:
            resp = r.xreadgroup(GROUP, CONSUMER, {STREAM: ">"}, count=16, block=5000)
        except RedisError as e:
            # idle-stream block timeout or a transient blip — stay alive, keep polling
            print(f"[bus] read retry: {e}", flush=True)
            time.sleep(1)
            continue
        for _stream, messages in resp or []:
            for msg_id, fields in messages:
                try:
                    payload = json.loads(fields[b"payload"])
                    out = run_inner(payload)
                    print(f"[bus] {out.get('signal_id')} -> {out.get('decision', {}).get('action')}", flush=True)
                    r.xack(STREAM, GROUP, msg_id)
                except Exception as e:                 # poison message -> ack + log, don't wedge
                    print(f"[bus] drop {msg_id}: {e}", flush=True)
                    r.xack(STREAM, GROUP, msg_id)


if __name__ == "__main__":
    consume()
