"""Illustrative software interlocks, not certified safety controls."""
import math
import time

class EstopLatch:
    def __init__(self):
        self.is_active = False
    def trigger(self):
        self.is_active = True
    def reset(self):
        self.is_active = False

class Deadman:
    def __init__(self):
        self.clients = {}
    def update(self, client, held):
        self.clients[client] = held is True
    def is_held(self, client):
        return self.clients.get(client, False)
    def remove(self, client):
        self.clients.pop(client, None)

class Watchdog:
    def __init__(self):
        self.last_command = -math.inf
    def feed(self, now=None):
        self.last_command = time.monotonic() if now is None else now
    def is_stale(self, now=None, timeout=0.3):
        return (time.monotonic() if now is None else now) - self.last_command > timeout
    def enforce(self, robot, now=None):
        if self.is_stale(now):
            robot.stop()

class RateLimiter:
    def __init__(self, max_hz=30):
        self.interval = 1 / max_hz
        self.last = -math.inf
        self.dropped_count = 0
    def accept(self, now=None):
        now = time.monotonic() if now is None else now
        if now - self.last + 1e-12 < self.interval:
            self.dropped_count += 1
            return False
        self.last = now
        return True

def clamp_velocity(linear, angular, limits=(1.0, 2.0)):
    if not all(math.isfinite(v) for v in (linear, angular)):
        raise ValueError('Velocity must be finite')
    return tuple(max(-limit, min(limit, value)) for value, limit in zip((linear, angular), limits))
