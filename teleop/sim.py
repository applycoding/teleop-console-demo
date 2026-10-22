"""Differential drive simulation, geometric lidar, and fixed-step scheduling."""
import asyncio
import math
import time
from dataclasses import dataclass, field

@dataclass
class World:
    width: float = 12.0
    height: float = 8.0
    rectangles: list = field(default_factory=lambda: [(3, 2, 1.3, 2), (7, 5, 2, 0.8), (9, 1, 0.8, 2)])
    circles: list = field(default_factory=lambda: [(5.7, 4, 0.65), (3, 6, 0.5)])

    def collides(self, x, y, radius):
        if x - radius < 0 or y - radius < 0 or x + radius > self.width or y + radius > self.height:
            return True
        for ox, oy, w, h in self.rectangles:
            if math.hypot(x - max(ox, min(x, ox+w)), y - max(oy, min(y, oy+h))) <= radius:
                return True
        return any(math.hypot(x-ox, y-oy) <= radius+r for ox, oy, r in self.circles)

@dataclass
class Robot:
    x: float = 1.5
    y: float = 4.0
    theta: float = 0.0
    v: float = 0.0
    w: float = 0.0
    wheel_base: float = 0.4
    radius: float = 0.22
    battery: float = 100.0
    world: World = field(default_factory=World)

    def stop(self):
        self.v = self.w = 0.0

    def step(self, dt):
        old = self.x, self.y, self.theta
        # Equivalent to wheel speeds v +/- w * wheel_base / 2.
        if abs(self.w) < 1e-9:
            self.x += self.v * math.cos(self.theta) * dt
            self.y += self.v * math.sin(self.theta) * dt
        else:
            self.x += self.v/self.w * (math.sin(self.theta+self.w*dt)-math.sin(self.theta))
            self.y -= self.v/self.w * (math.cos(self.theta+self.w*dt)-math.cos(self.theta))
        self.theta = (self.theta + self.w*dt + math.pi) % (2*math.pi) - math.pi
        self.battery = max(0, self.battery - dt*(abs(self.v)*0.03 + abs(self.w)*0.01))
        if self.world.collides(self.x, self.y, self.radius):
            self.x, self.y, self.theta = old
            self.stop()
        if self.battery == 0:
            self.stop()

def scan(robot, world, n_rays=180, max_range=10.0):
    ranges = []
    for i in range(n_rays):
        angle = robot.theta - math.pi + 2*math.pi*i/n_rays
        dx, dy = math.cos(angle), math.sin(angle)
        hits = [max_range]
        for wall, origin, direction in ((0, robot.x, dx), (world.width, robot.x, dx), (0, robot.y, dy), (world.height, robot.y, dy)):
            if abs(direction) > 1e-12:
                t = (wall-origin)/direction
                if t >= 0:
                    hits.append(t)
        for x, y, w, h in world.rectangles:
            lo, hi = -math.inf, math.inf
            for origin, direction, lower, upper in ((robot.x, dx, x, x+w), (robot.y, dy, y, y+h)):
                if abs(direction) < 1e-12:
                    if not lower <= origin <= upper:
                        hi = -math.inf
                else:
                    a, b = sorted(((lower-origin)/direction, (upper-origin)/direction))
                    lo, hi = max(lo, a), min(hi, b)
            if hi >= max(lo, 0):
                hits.append(max(0, lo))
        for x, y, radius in world.circles:
            ox, oy = robot.x-x, robot.y-y
            b = ox*dx+oy*dy
            discriminant = b*b-(ox*ox+oy*oy-radius*radius)
            if discriminant >= 0:
                t = -b-math.sqrt(discriminant)
                if t >= 0:
                    hits.append(t)
        ranges.append(min(hits))
    return ranges

class PhysicsLoop:
    def __init__(self, robot, before_step=lambda: None, hz=50):
        self.robot, self.before_step, self.dt = robot, before_step, 1/hz
    async def run(self):
        previous, accumulator = time.monotonic(), 0.0
        while True:
            now = time.monotonic()
            accumulator += now-previous
            previous = now
            while accumulator >= self.dt:
                self.before_step()
                self.robot.step(self.dt)
                accumulator -= self.dt
            await asyncio.sleep(max(0, self.dt-accumulator))
