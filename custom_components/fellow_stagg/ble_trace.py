"""Bounded, opt-in, memory-only capture of known status/settings records."""
from collections import deque
from time import monotonic

from .protocol import B1, B5


class BleTrace:
    def __init__(self):
        self.started = None
        self.deadline = 0
        self.active = False
        self.reason = "not_started"
        self.events = deque(maxlen=300)
        self.dropped = 0

    def start(self, duration):
        if self.active:
            raise ValueError("A BLE recording is already active")
        if isinstance(duration, bool) or not isinstance(duration, int) or not 10 <= duration <= 120:
            raise ValueError("Duration must be 10–120 seconds")
        self.started = monotonic()
        self.deadline = self.started + duration
        self.active = True
        self.reason = "recording"
        self.events.clear()
        self.dropped = 0

    def stop(self, reason="stopped"):
        self.active = False
        self.reason = reason

    def record(self, characteristic, raw, source):
        if not self.active:
            return
        if monotonic() >= self.deadline:
            self.stop("duration_elapsed")
            return
        # Never collect identity/network characteristics or arbitrary payloads.
        size = {B1: 16, B5: 17}.get(characteristic)
        if size is None or len(raw) != size:
            return
        if len(self.events) == self.events.maxlen:
            self.dropped += 1
        self.events.append({"seconds": round(monotonic() - self.started, 3),
                            "characteristic": "B1" if characteristic == B1 else "B5",
                            "source": source, "hex": bytes(raw).hex(" ")})

    def result(self):
        return {"active": self.active, "reason": self.reason,
                "dropped": self.dropped, "frames": list(self.events)}
