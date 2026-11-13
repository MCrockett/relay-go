"""What one snapshot build cost: its time, CPU and the processes it started (dashboard-scan D8, R12)."""
import contextlib
import contextvars
import os
import resource
import threading
import time

_ACTIVE = contextvars.ContextVar("relay_build_stats", default=None)


def _cpu():
    own, children = resource.getrusage(resource.RUSAGE_SELF), resource.getrusage(resource.RUSAGE_CHILDREN)
    return own.ru_utime + own.ru_stime + children.ru_utime + children.ru_stime


class Collector:
    def __init__(self):
        self._lock = threading.Lock()
        self.processes = {"git": 0, "gh": 0, "other": 0}
        self.fetched, self.hits, self.misses = [], 0, 0
        self._start, self._cpu = time.monotonic(), _cpu()

    def count(self, argv):
        word = str(argv[0]) if argv else ""
        gh = os.environ.get("RELAY_GH_BIN")
        kind = ("git" if os.path.basename(word) == "git" else
                "gh" if word == "gh" or (gh and word == gh) else "other")
        with self._lock:
            self.processes[kind] += 1

    def finish(self):
        with self._lock:
            return {"seconds": round(time.monotonic() - self._start, 3),
                    "cpu_seconds": round(float(max(0.0, _cpu() - self._cpu)), 3),
                    "processes": dict(self.processes), "fetched": list(self.fetched),
                    "cache": {"hits": self.hits, "misses": self.misses}}


@contextlib.contextmanager
def collect():
    """Count every process started in this context. Worker threads count when run in a copy of it."""
    collector = Collector()
    token = _ACTIVE.set(collector)
    try:
        yield collector
    finally:
        _ACTIVE.reset(token)


def count(argv):
    collector = _ACTIVE.get()
    if collector is not None:
        collector.count(argv)


def note_fetch(repo):
    collector = _ACTIVE.get()
    if collector is not None:
        with collector._lock:
            collector.fetched.append(repo)


def hit():
    collector = _ACTIVE.get()
    if collector is not None:
        with collector._lock:
            collector.hits += 1


def miss():
    collector = _ACTIVE.get()
    if collector is not None:
        with collector._lock:
            collector.misses += 1
