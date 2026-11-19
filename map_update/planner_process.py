"""A timed JSON-lines bridge to a trusted local planner/controller executable."""
import json
import math
import selectors
import subprocess
import os


class PlannerProcess:
    def __init__(self, command, stderr, timeout=10.):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Worker timeout must be positive and finite")
        self.timeout = timeout
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=stderr, bufsize=0)
        self.buffer = b''

    def compute(self, request):
        import time
        if self.process.poll() is not None:
            raise RuntimeError('Planner/controller process exited')
        encoded = json.dumps(request, allow_nan=False).encode() + b'\n'
        deadline = time.monotonic() + self.timeout
        # Both writes and reads are bounded, including a worker that stops reading.
        os.set_blocking(self.process.stdin.fileno(), False)
        os.set_blocking(self.process.stdout.fileno(), False)
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdin, selectors.EVENT_WRITE)
            sent = 0
            while sent < len(encoded):
                if time.monotonic() >= deadline or not selector.select(max(0., deadline-time.monotonic())):
                    raise TimeoutError('Planner request timed out')
                sent += os.write(self.process.stdin.fileno(), encoded[sent:sent+65536])
            selector.unregister(self.process.stdin)
            selector.register(self.process.stdout, selectors.EVENT_READ)
            while b'\n' not in self.buffer:
                if time.monotonic() >= deadline or not selector.select(max(0., deadline-time.monotonic())):
                    raise TimeoutError('Planner response timed out')
                chunk = os.read(self.process.stdout.fileno(), 65536)
                if not chunk:
                    raise RuntimeError('Planner closed its response stream')
                self.buffer += chunk
                if len(self.buffer) > 1024 * 1024:
                    raise ValueError('Planner response exceeds 1 MiB')
        line, self.buffer = self.buffer.split(b'\n', 1)
        result = json.loads(line)
        if not isinstance(result, dict) or set(result) != {'plans', 'actions', 'implementation', 'state_version'}:
            raise ValueError('Worker response requires plans, actions, implementation and state_version')
        if result['state_version'] != request['observation']['state_version']:
            raise ValueError('Worker response belongs to a different observation')
        return result

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.process.stdin.close()
        self.process.stdout.close()
