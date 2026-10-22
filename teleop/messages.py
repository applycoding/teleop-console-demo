"""ROS-style topic envelopes and payload schemas."""
import time
from typing import TypedDict

CMD_VEL = '/cmd_vel'
ODOM = '/odom'
SCAN = '/scan'
BATTERY_STATE = '/battery_state'
DIAGNOSTICS = '/diagnostics'

class Command(TypedDict):
    linear: float
    angular: float
    deadman: bool

class Odometry(TypedDict):
    x: float
    y: float
    theta: float
    v: float
    w: float

class Scan(TypedDict):
    angle_min: float
    angle_max: float
    ranges: list[float]

class BatteryState(TypedDict):
    percentage: float
    voltage: float

class Diagnostics(TypedDict):
    estop: bool
    watchdog_tripped: bool
    link: str
    cmd_rate_hz: float
    dropped_cmds: int
    rtt_ms: float

def make_message(topic, data):
    return {'topic': topic, 'stamp': time.time(), 'data': data}
