"""Per-connection asynchronous command delay queue."""
import asyncio
import itertools
import random
import time
from dataclasses import dataclass

@dataclass
class NetworkConditions:
    latency_ms: float = 0
    jitter_ms: float = 0
    loss_pct: float = 0

@dataclass
class DelayedDelivery:
    message: dict
    delay_ms: float

class NetworkEmulator:
    def __init__(self, conditions=None, rng=None):
        self.conditions = conditions or NetworkConditions()
        self.rng = rng or random.Random()
        self.queue = asyncio.PriorityQueue()
        self.sequence = itertools.count()
        self.dropped_count = 0
    def apply(self, message):
        c = self.conditions
        if self.rng.random()*100 < c.loss_pct:
            self.dropped_count += 1
            return None
        return DelayedDelivery(message, max(0, c.latency_ms+self.rng.uniform(-c.jitter_ms, c.jitter_ms)))
    async def submit(self, message):
        delivery = self.apply(message)
        if delivery:
            now = time.monotonic()
            await self.queue.put((now+delivery.delay_ms/1000, next(self.sequence), now, delivery))
    async def run(self, deliver):
        # Poll the priority queue so a newly queued earlier packet can overtake.
        while True:
            now = time.monotonic()
            while not self.queue.empty() and self.queue._queue[0][0] <= now:
                _, _, submitted, delivery = self.queue.get_nowait()
                await deliver(delivery.message, (time.monotonic()-submitted)*1000)
            await asyncio.sleep(0.002)
