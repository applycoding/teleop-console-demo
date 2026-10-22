"""Append-only session recording and paced telemetry replay."""
import asyncio
import json
from pathlib import Path
import uuid

class Recorder:
    def __init__(self, directory='recordings'):
        self.directory = Path(directory)
        self.file = None
        self.session_id = None
    def start(self):
        self.stop()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.session_id = uuid.uuid4().hex
        self.file = (self.directory / (self.session_id+'.jsonl')).open('a', encoding='utf-8')
        return self.session_id
    def record(self, message):
        if self.file:
            self.file.write(json.dumps(message, allow_nan=False)+'\n')
            self.file.flush()
    def stop(self):
        if self.file:
            self.file.close()
            self.file = None
    def list_recordings(self):
        return list_recordings(self.directory)

def list_recordings(directory='recordings'):
    result = []
    for path in sorted(Path(directory).glob('*.jsonl')):
        lines = path.read_text().splitlines()
        result.append({'id': path.stem, 'start_time': json.loads(lines[0])['stamp'] if lines else path.stat().st_mtime, 'size': path.stat().st_size, 'message_count': len(lines)})
    return result

class Replayer:
    def __init__(self, path):
        self.path = Path(path)
    async def messages(self, paced=True):
        previous = None
        with self.path.open(encoding='utf-8') as source:
            for line in source:
                message = json.loads(line)
                if paced and previous is not None:
                    await asyncio.sleep(max(0, message['stamp']-previous))
                previous = message['stamp']
                yield message
