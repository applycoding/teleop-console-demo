import time
import pytest
from fastapi.testclient import TestClient
from teleop.server import create_app


def receive_topic(ws, topic, predicate=lambda d: True):
    for _ in range(150):
        message = ws.receive_json()
        if message['topic'] == topic and predicate(message['data']):
            return message['data']
    raise AssertionError('Expected telemetry not received')


def command(ws, held=True, linear=.8):
    ws.send_json({'topic': '/cmd_vel', 'data': {'linear': linear, 'angular': 0, 'deadman': held}})


def wait_until(predicate, timeout=1.0, interval=.005):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


@pytest.fixture
def running(tmp_path):
    app = create_app(recordings_dir=tmp_path)
    with TestClient(app) as client:
        yield client, app.state.console


def test_health_and_static(running):
    client, _ = running
    assert client.get('/api/health').json()['status'] == 'ok'
    assert 'Mission control' in client.get('/').text
    assert client.get('/app.js').status_code == 200


def test_operator_observer_motion_estop(running):
    client, console = running
    with client.websocket_connect('/ws') as operator:
        assert receive_topic(operator, '/diagnostics')['role'] == 'operator'
        with client.websocket_connect('/ws') as observer:
            assert receive_topic(observer, '/diagnostics')['role'] == 'observer'
            command(observer)
            assert 'rejected' in receive_topic(observer, '/diagnostics', lambda d: 'reason' in d)['reason']
            command(operator)
            odom = receive_topic(operator, '/odom', lambda d: d['x'] > 1.51)
            assert odom['v'] > 0
            assert client.post('/api/estop').json()['estop']
            command(operator)
            time.sleep(.06)
            assert console.robot.v == 0
            assert console.estop.is_active
            result = client.post('/api/reset').json()
            assert not result['estop']
            assert result['battery'] == 100


def test_deadman_release_bypasses_loss_and_delay(running):
    client, console = running
    with client.websocket_connect('/ws') as ws:
        command(ws)
        receive_topic(ws, '/odom', lambda d: d['v'] > 0)
        client.post('/api/network', json={'latency_ms': 200, 'loss_pct': 100})
        command(ws, False)
        time.sleep(.03)
        assert console.robot.v == 0
        assert not console.deadman.is_held(console.operator)


def test_watchdog_and_disconnect(running):
    client, console = running
    with client.websocket_connect('/ws') as ws:
        command(ws)
        receive_topic(ws, '/odom', lambda d: d['v'] > 0)
        receive_topic(ws, '/diagnostics', lambda d: d.get('watchdog_tripped') is True)
        receive_topic(ws, '/odom', lambda d: d['v'] == 0)
        assert console.robot.v == 0
        command(ws)
        receive_topic(ws, '/odom', lambda d: d['v'] > 0)
    assert wait_until(lambda: console.robot.v == 0)
    assert console.operator is None


def test_ping_network_and_validation(running):
    client, _ = running
    assert client.post('/api/network', json={'loss_pct': 101}).status_code == 422
    client.post('/api/network', json={'latency_ms': 50})
    assert client.get('/api/state').json()['network']['latency_ms'] == 50
    with client.websocket_connect('/ws') as ws:
        started = time.monotonic()
        ws.send_json({'topic': '/ping', 'data': {'id': 'test', 't': 123}})
        pong = receive_topic(ws, '/pong')
        assert pong['id'] == 'test'
        assert pong['applied_delay_ms'] >= 50
        assert time.monotonic()-started >= .05
        ws.send_json({'topic': '/cmd_vel', 'data': {'linear': 'bad', 'angular': 0, 'deadman': True}})
        assert 'numbers' in receive_topic(ws, '/diagnostics', lambda d: 'numbers' in d.get('reason', ''))['reason']


def test_stale_packets_cannot_move_robot(running):
    client, console = running
    client.post('/api/network', json={'latency_ms': 350})
    with client.websocket_connect('/ws') as ws:
        command(ws)
        time.sleep(.42)
        assert console.robot.x == 1.5
        assert console.robot.v == 0


def test_replay_streams_odom_over_ws(running):
    client, _ = running
    session = client.post('/api/recordings/start').json()['id']
    with client.websocket_connect('/ws') as ws:
        command(ws)
        receive_topic(ws, '/odom', lambda d: d['x'] > 1.51)
        client.post('/api/recordings/stop')
    assert client.post('/api/replay/' + session).json()['status'] == 'replay'
    with client.websocket_connect('/ws') as ws:
        receive_topic(ws, '/diagnostics', lambda d: d.get('replay') is True)
        odom = receive_topic(ws, '/odom')
        assert 'x' in odom and 'y' in odom
    assert client.post('/api/replay/stop').json()['status'] == 'stopped'


def test_record_and_replay_isolation(running):
    client, console = running
    session = client.post('/api/recordings/start').json()['id']
    with client.websocket_connect('/ws') as ws:
        command(ws)
        receive_topic(ws, '/odom', lambda d: d['x'] > 1.51)
        client.post('/api/recordings/stop')
        items = client.get('/api/recordings').json()
        assert items[0]['message_count'] > 0
        content = client.get('/api/recordings/'+session).text
        assert '/cmd_vel' in content and '/odom' in content
        assert client.post('/api/replay/'+session).status_code == 200
        command(ws)
        assert 'blocked' in receive_topic(ws, '/diagnostics', lambda d: 'blocked' in d.get('reason', ''))['reason']
        assert console.robot.v == 0
        assert client.post('/api/replay/stop').status_code == 200
        assert not console.replaying
    assert client.get('/api/recordings/not-a-session').status_code == 404


def test_deterministic_factory(tmp_path):
    app = create_app(start_loops=False, recordings_dir=tmp_path)
    with TestClient(app):
        console = app.state.console
        console.robot.v = 1
        console.robot.step(.02)
        assert console.robot.x == pytest.approx(1.52)
        console.safety_step()
        assert console.robot.v == 0
