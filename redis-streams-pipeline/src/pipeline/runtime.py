"""Process plumbing shared by decider.py and worker.py: graceful shutdown, recovery from Redis outages,
and XAUTOCLAIM of messages abandoned by dead consumers."""
import signal
import threading

import redis

from pipeline import config

stop = threading.Event()


def install_signal_handlers(label):
    """First Ctrl+C / SIGTERM: finish the message in flight, then exit. Second: abort immediately
    (the in-flight message stays pending and is reclaimed later)."""
    def handler(signum, frame):
        if stop.is_set():
            raise KeyboardInterrupt
        stop.set()
        print(f"[{label}] stopping after the current message (Ctrl+C again to abort)", flush=True)

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), handler)


def run_loop(r, step, label):
    """Call step() until asked to stop. Survives Redis being down or restarted (backoff, then resume) and
    streams/groups disappearing (NOGROUP: recreate them). Anything else is a bug and propagates."""
    backoff = 1.0
    while not stop.is_set():
        try:
            step()
            backoff = 1.0
        except (redis.ConnectionError, redis.TimeoutError) as e:
            print(f"[{label}] redis unavailable ({e}); retrying in {backoff:.0f}s", flush=True)
            stop.wait(backoff)
            backoff = min(backoff * 2, 30)
        except redis.ResponseError as e:
            if "NOGROUP" not in str(e):
                raise
            print(f"[{label}] stream or group missing; recreating", flush=True)
            config.ensure_groups(r)


def reclaim(r, stream, group, consumer, handler, min_idle_ms):
    """XAUTOCLAIM everything idle longer than min_idle_ms and pass each message to handler(msg_id, fields).
    Returns how many were handled."""
    cursor, total = "0-0", 0
    while not stop.is_set():
        cursor, msgs, _deleted = r.xautoclaim(stream, group, consumer, min_idle_time=min_idle_ms,
                                              start_id=cursor, count=config.BATCH)
        for msg_id, fields in msgs:
            if not fields:  # the entry was trimmed from the stream; nothing left to process
                r.xack(stream, group, msg_id)
                continue
            handler(msg_id, fields)
            total += 1
            if stop.is_set():
                return total
        if cursor in ("0-0", "0"):
            break
    return total
