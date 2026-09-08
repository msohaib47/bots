"""
Thin ZeroMQ PUB/SUB helpers shared by every DayTradingBotV2 service -- see
PLAN.md's "Inter-process transport" and "Failure handling" sections.

Wire format: multipart messages, [topic_bytes, json_payload_bytes]. ZMQ's
native SUB-side topic filtering matches on a byte-prefix of the first frame,
so subscribing to b'signal.' receives every b'signal.SPY', b'signal.QQQ', ...
message without the subscriber having to parse and discard anything itself.

Heartbeats: every Publisher can call `heartbeat(source)` periodically
(recommended ~10s, see PLAN.md) to publish a liveness message on
`heartbeat.<source>` even when there's nothing else to say. `source` must be
unique per publisher (e.g. 'signal', 'sizing.start200') so a subscriber
connected to multiple publishers can track each one's liveness independently
via `Subscriber.is_alive(f'heartbeat.<source>')` -- a bare 'heartbeat' topic
shared by every publisher would make it impossible to tell which one just
went quiet.
"""
import json
import time

import zmq

HEARTBEAT_PREFIX = 'heartbeat'


def heartbeat_topic(source: str) -> str:
    return f'{HEARTBEAT_PREFIX}.{source}'


class Publisher:
    def __init__(self, endpoint: str):
        self._ctx = zmq.Context.instance()
        self._sock = self._ctx.socket(zmq.PUB)
        self._sock.bind(endpoint)

    def publish(self, topic: str, payload: dict):
        self._sock.send_multipart([topic.encode(), json.dumps(payload).encode()])

    def heartbeat(self, source: str):
        self.publish(heartbeat_topic(source), {'source': source, 'ts': time.time()})

    def close(self):
        self._sock.close()


class Subscriber:
    def __init__(self, endpoints: list[str], topics: list[str]):
        """`endpoints`: one or more `tcp://host:port` addresses to connect to.
        `topics`: topic prefixes to subscribe to (pass '' to receive everything)."""
        self._ctx = zmq.Context.instance()
        self._sock = self._ctx.socket(zmq.SUB)
        for ep in endpoints:
            self._sock.connect(ep)
        for topic in topics:
            self._sock.setsockopt(zmq.SUBSCRIBE, topic.encode())
        self._last_seen: dict[str, float] = {}

    def poll(self, timeout_ms: int = 0) -> list[tuple[str, dict]]:
        """Drains every message currently available (non-blocking by default,
        or waits up to timeout_ms for the first one). Returns [(topic, payload), ...]."""
        received = []
        if timeout_ms:
            if not self._sock.poll(timeout_ms):
                return received
        while True:
            try:
                topic_b, payload_b = self._sock.recv_multipart(flags=zmq.NOBLOCK)
            except zmq.Again:
                break
            topic = topic_b.decode()
            self._last_seen[topic] = time.time()
            received.append((topic, json.loads(payload_b.decode())))
        return received

    def last_seen(self, topic: str) -> float | None:
        return self._last_seen.get(topic)

    def is_alive(self, topic: str, max_age_seconds: float = 30) -> bool:
        seen = self._last_seen.get(topic)
        return seen is not None and (time.time() - seen) < max_age_seconds

    def close(self):
        self._sock.close()
