import asyncio
from teleop.netem import NetworkConditions, NetworkEmulator


def test_zero_passes_unchanged():
    message = {'topic': '/cmd_vel'}
    delivery = NetworkEmulator().apply(message)
    assert delivery.message is message
    assert delivery.delay_ms == 0


def test_total_loss():
    net = NetworkEmulator(NetworkConditions(loss_pct=100))
    assert all(net.apply({}) is None for _ in range(100))
    assert net.dropped_count == 100


def test_jitter_bounds():
    net = NetworkEmulator(NetworkConditions(100, 30, 0))
    delays = [net.apply({}).delay_ms for _ in range(500)]
    assert all(70 <= d <= 130 for d in delays)
    assert min(delays) < 80 and max(delays) > 120
    net.conditions.latency_ms = 0
    assert all(0 <= net.apply({}).delay_ms <= 30 for _ in range(100))


def test_delay_queue_allows_earlier_packet_to_overtake():
    async def run():
        net = NetworkEmulator(NetworkConditions(80, 0, 0))
        deliveries = []
        async def deliver(message, delay): deliveries.append((message, delay))
        task = asyncio.create_task(net.run(deliver))
        await net.submit({'id': 'slow'})
        net.conditions.latency_ms = 0
        await net.submit({'id': 'fast'})
        await asyncio.sleep(.12)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert [d[0]['id'] for d in deliveries] == ['fast', 'slow']
        assert deliveries[1][1] >= 80
    asyncio.run(run())
