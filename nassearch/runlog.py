"""Timestamped logging that survives being run as a detached job.

Every stage writes here as well as to stdout, and every line is flushed, so
`tail -f <out>/_meta/run.log` shows live progress even when the process was
started with nohup and stdout went nowhere useful.
"""

import os
import sys
import time


class RunLog:
    def __init__(self, out_dir, echo=True):
        meta = os.path.join(out_dir, "_meta")
        os.makedirs(meta, exist_ok=True)
        self.path = os.path.join(meta, "run.log")
        self._fh = open(self.path, "a", 1)  # line-buffered
        self._echo = echo
        self.started = time.time()

    def __call__(self, message):
        line = "%s  %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), message)
        self._fh.write(line + "\n")
        if self._echo:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()

    def close(self):
        self._fh.close()


class Progress:
    """A heartbeat for the long passes, so a quiet log never looks like a hang."""

    def __init__(self, log, label, total, every=2000, every_seconds=30):
        self.log, self.label, self.total = log, label, total
        self.every, self.every_seconds = every, every_seconds
        self.done = self.bytes = 0
        self.started = self.last = time.time()

    def advance(self, size=0):
        self.done += 1
        self.bytes += size
        now = time.time()
        if self.done % self.every and now - self.last < self.every_seconds:
            return
        self.last = now
        elapsed = now - self.started
        rate = self.done / elapsed if elapsed else 0
        remaining = (self.total - self.done) / rate if rate else 0
        self.log("    %s %d/%d (%.0f%%)  %.1f GiB read  %.0f/s  eta %s"
                 % (self.label, self.done, self.total,
                    100.0 * self.done / self.total if self.total else 100.0,
                    self.bytes / float(1 << 30), rate, _hms(remaining)))


def _hms(seconds):
    seconds = int(seconds)
    return "%d:%02d:%02d" % (seconds // 3600, seconds % 3600 // 60, seconds % 60)
