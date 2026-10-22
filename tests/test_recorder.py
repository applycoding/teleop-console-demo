import asyncio
from teleop.recorder import Recorder, Replayer, list_recordings


def test_roundtrip(tmp_path):
    recorder = Recorder(tmp_path)
    session = recorder.start()
    messages = [{'topic': '/odom', 'stamp': 10+i*.001, 'data': {'x': i}} for i in range(3)]
    for message in messages: recorder.record(message)
    recorder.stop()
    async def read(paced):
        return [message async for message in Replayer(tmp_path/(session+'.jsonl')).messages(paced)]
    assert asyncio.run(read(False)) == messages
    assert asyncio.run(read(True)) == messages
    item = list_recordings(tmp_path)[0]
    assert item['id'] == session
    assert item['message_count'] == 3
    assert item['start_time'] == 10
    assert item['size'] > 0
