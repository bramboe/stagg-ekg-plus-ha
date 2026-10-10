"""Bounded extraction of schedule settings from experimental console notifications."""
import re


class ScheduleConsoleCapture:
    def __init__(self):
        self.buffer = bytearray()
        self.notifications = 0
        self.truncated = False

    def feed(self, raw):
        self.notifications += 1
        remaining = 4096 - len(self.buffer)
        self.truncated |= len(raw) > remaining
        self.buffer.extend(raw[:remaining])

    def result(self):
        # Do not expose arbitrary console text, network identity or credentials.
        text = self.buffer.decode("ascii", errors="replace")
        settings = {}
        for match in re.finditer(r"(?m)^\s*st:\s*(Repeat_sched|schedon|schtime|schtempr)\s+(-?\d+)(?=\s|$)", text):
            settings[match[1]] = int(match[2])
        return {"notifications": self.notifications, "captured_bytes": len(self.buffer),
                "truncated": self.truncated, "settings": settings}
