import asyncio
import math
import pytest
from teleop.sim import PhysicsLoop, Robot, World, scan


def empty_world():
    return World(width=100, height=100, rectangles=[], circles=[])


def test_straight():
    robot = Robot(x=10, y=10, v=1, world=empty_world())
    robot.step(1)
    assert (robot.x, robot.y) == pytest.approx((11, 10))


def test_rotation():
    robot = Robot(x=10, y=10, w=1, world=empty_world())
    robot.step(1)
    assert (robot.x, robot.y, robot.theta) == pytest.approx((10, 10, 1))


def test_arc():
    robot = Robot(x=10, y=10, v=1, w=1, world=empty_world())
    robot.step(math.pi/2)
    assert (robot.x, robot.y, robot.theta) == pytest.approx((11, 11, math.pi/2))


@pytest.mark.parametrize('world', [World(rectangles=[(2, 3, 1, 2)], circles=[]), World(rectangles=[], circles=[(2.4, 4, .4)])])
def test_collision(world):
    robot = Robot(x=1.5, y=4, v=1, world=world)
    robot.step(.5)
    assert robot.x == 1.5
    assert robot.v == 0


def test_wall_and_obstacle_scan():
    world = World(width=10, height=10, rectangles=[], circles=[])
    robot = Robot(x=5, y=5, world=world)
    assert scan(robot, world, n_rays=4) == pytest.approx([5]*4)
    world.rectangles = [(7, 4, 1, 2)]
    assert scan(robot, world, n_rays=4)[2] == pytest.approx(2)
    world.rectangles = []
    world.circles = [(8, 5, 1)]
    assert scan(robot, world, n_rays=4)[2] == pytest.approx(2)


def test_battery_drain_and_floor():
    robot = Robot(v=1, battery=.001)
    robot.step(.1)
    assert robot.battery == 0
    assert robot.v == 0
    robot.step(1)
    assert robot.battery == 0


def test_physics_uses_fixed_steps():
    class Probe:
        def __init__(self): self.steps = []
        def step(self, dt): self.steps.append(dt)
    async def run():
        probe = Probe()
        task = asyncio.create_task(PhysicsLoop(probe).run())
        await asyncio.sleep(.075)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert len(probe.steps) >= 2
        assert all(dt == .02 for dt in probe.steps)
    asyncio.run(run())
