"""Single-process simulated teleoperation server."""
import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
import logging
import math
import os
from pathlib import Path
import time
import uuid

logger = logging.getLogger(__name__)

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .messages import make_message
from .netem import NetworkConditions, NetworkEmulator
from .recorder import Recorder, Replayer
from .safety import Deadman, EstopLatch, RateLimiter, Watchdog, clamp_velocity
from .sim import PhysicsLoop, Robot, World, scan

ROOT = Path(__file__).resolve().parent.parent

class NetworkUpdate(BaseModel):
    latency_ms: float = Field(0, ge=0, le=3000, allow_inf_nan=False)
    jitter_ms: float = Field(0, ge=0, le=3000, allow_inf_nan=False)
    loss_pct: float = Field(0, ge=0, le=100, allow_inf_nan=False)

class Console:
    def __init__(self, recordings_dir):
        self.world = World()
        self.robot = Robot(world=self.world)
        self.estop, self.deadman, self.watchdog = EstopLatch(), Deadman(), Watchdog()
        self.network = NetworkConditions()
        self.clients = {}
        self.operator = None
        self.recorder = Recorder(recordings_dir)
        self.replay_task = None
        self.replaying = False
        self.epoch = 0

    def stop(self):
        self.robot.stop()
        self.epoch += 1

    def safety_step(self):
        if self.estop.is_active or self.replaying or not self.operator or not self.deadman.is_held(self.operator):
            self.robot.stop()
        self.watchdog.enforce(self.robot)

    async def send(self, client, message):
        async with client['send_lock']:
            await client['ws'].send_json(message)

    def drop_client(self, cid):
        client = self.clients.pop(cid, None)
        if client:
            client['task'].cancel()
        self.deadman.remove(cid)
        if self.operator == cid:
            self.stop()
            self.operator = next(iter(self.clients), None)

    async def broadcast(self, message):
        self.recorder.record(message)
        for cid, client in list(self.clients.items()):
            try:
                await asyncio.wait_for(self.send(client, message), timeout=0.2)
            except Exception:
                self.drop_client(cid)

    def state(self):
        r = self.robot
        return {'pose': {'x': r.x, 'y': r.y, 'theta': r.theta}, 'battery': r.battery,
                'estop': self.estop.is_active, 'connected_clients': len(self.clients),
                'replay': self.replaying, 'world': asdict(self.world)}

    async def telemetry(self):
        while True:
            try:
                if not self.replaying:
                    r = self.robot
                    await self.broadcast(make_message('/odom', {'x': r.x, 'y': r.y, 'theta': r.theta, 'v': r.v, 'w': r.w}))
                    await self.broadcast(make_message('/scan', {'angle_min': -math.pi, 'angle_max': math.pi, 'ranges': scan(r, self.world)}))
                    await self.broadcast(make_message('/battery_state', {'percentage': r.battery, 'voltage': 10.5+2.1*r.battery/100}))
                for cid, client in list(self.clients.items()):
                    recent = [t for t in client['accepted'] if time.monotonic()-t < 1]
                    client['accepted'] = recent
                    data = {'estop': self.estop.is_active, 'watchdog_tripped': self.watchdog.is_stale(),
                            'deadman': self.deadman.is_held(self.operator), 'link': 'ok',
                            'cmd_rate_hz': len(recent), 'dropped_cmds': client['limiter'].dropped_count+client['net'].dropped_count,
                            'rtt_ms': client['rtt'], 'role': 'operator' if cid == self.operator else 'observer', 'replay': self.replaying}
                    message = make_message('/diagnostics', data)
                    self.recorder.record(message)
                    try:
                        await asyncio.wait_for(self.send(client, message), 0.2)
                    except Exception:
                        self.drop_client(cid)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception('telemetry loop iteration failed')
            await asyncio.sleep(0.05)

    async def replay(self, path):
        try:
            async for message in Replayer(path).messages():
                if message['topic'] in ('/odom', '/scan', '/battery_state'):
                    await self.broadcast(message)
        finally:
            self.replaying = False
            self.stop()


def _log_task_crash(task):
    if not task.cancelled() and task.exception() is not None:
        logger.error('Background task %r crashed', task, exc_info=task.exception())

def create_app(start_loops=True, recordings_dir=None):
    console = Console(recordings_dir or ROOT/'recordings')

    @asynccontextmanager
    async def lifespan(app):
        tasks = []
        if start_loops:
            tasks = [asyncio.create_task(PhysicsLoop(console.robot, console.safety_step).run()), asyncio.create_task(console.telemetry())]
            for task in tasks:
                task.add_done_callback(_log_task_crash)
        yield
        for client in list(console.clients.values()):
            client['task'].cancel()
            tasks.append(client['task'])
        if console.replay_task:
            tasks.append(console.replay_task)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        console.recorder.stop()

    app = FastAPI(title='Teleop Console Demo', lifespan=lifespan)
    app.state.console = console

    @app.get('/api/health')
    async def health():
        return {'status': 'ok', 'simulation': True}

    @app.get('/api/state')
    async def state():
        return console.state()

    @app.post('/api/estop')
    async def estop():
        console.estop.trigger()
        console.stop()
        return console.state()

    @app.post('/api/reset')
    async def reset():
        console.stop()
        console.estop.reset()
        fresh = Robot(world=console.world)
        console.robot.__dict__.update(fresh.__dict__)
        console.deadman.clients.clear()
        console.watchdog = Watchdog()
        return console.state()

    @app.post('/api/network')
    async def network(update: NetworkUpdate):
        for key, value in update.model_dump().items():
            setattr(console.network, key, value)
        return asdict(console.network)

    @app.post('/api/recordings/start')
    async def recording_start():
        if console.replaying:
            raise HTTPException(409, 'Stop replay before recording')
        return {'id': console.recorder.start()}

    @app.post('/api/recordings/stop')
    async def recording_stop():
        console.recorder.stop()
        return {'status': 'stopped'}

    @app.get('/api/recordings')
    async def recordings():
        return console.recorder.list_recordings()

    def recording_path(recording_id):
        if not recording_id or any(c not in '0123456789abcdef' for c in recording_id):
            raise HTTPException(404, 'Recording not found')
        path = console.recorder.directory/(recording_id+'.jsonl')
        if not path.is_file():
            raise HTTPException(404, 'Recording not found')
        return path

    @app.get('/api/recordings/{recording_id}')
    async def recording_get(recording_id: str):
        return FileResponse(recording_path(recording_id), media_type='application/x-ndjson')

    async def stop_replay():
        if console.replay_task:
            console.replay_task.cancel()
            with suppress(asyncio.CancelledError):
                await console.replay_task
            console.replay_task = None
        console.replaying = False
        console.stop()

    @app.post('/api/replay/stop')
    async def replay_stop():
        await stop_replay()
        return {'status': 'stopped'}

    @app.post('/api/replay/{recording_id}')
    async def replay_start(recording_id: str):
        path = recording_path(recording_id)
        await stop_replay()
        console.recorder.stop()
        console.stop()
        console.replaying = True
        console.replay_task = asyncio.create_task(console.replay(path))
        return {'status': 'replay', 'id': recording_id}

    @app.websocket('/ws')
    async def websocket(ws: WebSocket):
        await ws.accept()
        cid = uuid.uuid4().hex
        if console.operator is None:
            console.operator = cid
        client = {'ws': ws, 'send_lock': asyncio.Lock(), 'limiter': RateLimiter(), 'net': NetworkEmulator(console.network), 'accepted': [], 'rtt': 0}
        console.clients[cid] = client

        async def diagnostic(reason):
            await console.send(client, make_message('/diagnostics', {'reason': reason, 'role': 'operator' if cid == console.operator else 'observer'}))

        async def deliver(packet, delay):
            message = packet['message']
            if message['topic'] == '/ping':
                client['rtt'] = delay
                await console.send(client, make_message('/pong', {**message['data'], 'applied_delay_ms': delay}))
                return
            if packet['epoch'] != console.epoch or time.monotonic()-packet['received'] > 0.3:
                return
            if not client['limiter'].accept():
                return
            if console.estop.is_active or console.replaying or not console.deadman.is_held(cid):
                return
            data = message['data']
            console.robot.v, console.robot.w = clamp_velocity(data['linear'], data['angular'])
            console.watchdog.feed()
            client['accepted'].append(time.monotonic())

        client['task'] = asyncio.create_task(client['net'].run(deliver))
        try:
            await diagnostic('Connected')
            while True:
                try:
                    message = await ws.receive_json()
                    if not isinstance(message, dict) or not isinstance(message.get('data'), dict):
                        raise ValueError('Expected topic and data object')
                    topic, data = message.get('topic'), message['data']
                    if topic == '/ping':
                        await client['net'].submit({'message': message})
                    elif topic == '/cmd_vel':
                        if cid != console.operator:
                            await diagnostic('Observer commands rejected')
                            continue
                        if type(data.get('deadman')) is not bool:
                            raise ValueError('deadman must be boolean')
                        linear, angular = data.get('linear'), data.get('angular')
                        if type(linear) not in (int, float) or type(angular) not in (int, float):
                            raise ValueError('Velocities must be numbers')
                        clamp_velocity(linear, angular)
                        console.recorder.record(make_message(topic, data))
                        console.deadman.update(cid, data['deadman'])
                        if not data['deadman']:
                            console.stop()
                            continue
                        if console.replaying:
                            await diagnostic('Live commands blocked during replay')
                            continue
                        await client['net'].submit({'message': message, 'epoch': console.epoch, 'received': time.monotonic()})
                    else:
                        await diagnostic('Unknown topic')
                except (ValueError, TypeError, KeyError) as error:
                    if cid == console.operator:
                        console.stop()
                    await diagnostic(str(error))
        except WebSocketDisconnect:
            pass
        finally:
            # Zero velocity and drop the client before any cancellable await, so a
            # concurrent teardown of this coroutine (e.g. the test client tearing
            # down the socket) can never skip this bookkeeping.
            console.clients.pop(cid, None)
            console.deadman.remove(cid)
            if console.operator == cid:
                console.stop()
                console.operator = next(iter(console.clients), None)
            client['task'].cancel()
            with suppress(asyncio.CancelledError):
                await asyncio.shield(asyncio.gather(client['task'], return_exceptions=True))

    app.mount('/', StaticFiles(directory=ROOT/'web', html=True), name='web')
    return app

app = create_app()

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=int(os.environ.get('PORT', '8000')))
