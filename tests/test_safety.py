import pytest
from teleop.safety import Deadman, EstopLatch, RateLimiter, Watchdog, clamp_velocity
from teleop.sim import Robot


def test_estop_latches():
    latch = EstopLatch()
    latch.trigger()
    latch.trigger()
    assert latch.is_active
    latch.reset()
    assert not latch.is_active


def test_deadman_per_client():
    deadman = Deadman()
    assert not deadman.is_held('a')
    deadman.update('a', True)
    assert deadman.is_held('a')
    assert not deadman.is_held('b')
    deadman.update('a', False)
    assert not deadman.is_held('a')


def test_watchdog_stops():
    watchdog = Watchdog()
    robot = Robot(v=1, w=1)
    watchdog.feed(1)
    assert not watchdog.is_stale(1.2)
    watchdog.enforce(robot, 1.301)
    assert robot.v == robot.w == 0


def test_limiter_counts_drops():
    limiter = RateLimiter()
    results = [limiter.accept(i/1000) for i in range(1000)]
    assert 29 <= sum(results) <= 30
    assert limiter.dropped_count == 1000-sum(results)


def test_clamp():
    assert clamp_velocity(4, -4) == (1, -2)
    assert clamp_velocity(.4, -.2, (.5, .5)) == (.4, -.2)
    with pytest.raises(ValueError):
        clamp_velocity(float('nan'), 0)
