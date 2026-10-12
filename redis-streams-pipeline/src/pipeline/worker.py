"""Generic consumer for one decision stream: log and ack. Replace `process` with real downstream work.
Messages abandoned by a dead worker are reclaimed after WORKER_RECLAIM_IDLE_MS.
usage: python run.py worker <stream> [NAME]"""
import argparse
import time

from pipeline import config, runtime

WORKER_RECLAIM_IDLE_MS = 60_000


def format_record(stream, msg_id, rec):
    try:
        conf = f"{float(rec.get('confidence')):.3f}"
    except (TypeError, ValueError):
        conf = rec.get("confidence", "?")
    return (f"{stream} {msg_id} id={rec.get('id')} label={rec.get('label')} conf={conf} "
            f"source={rec.get('source', '?')} low_confidence={rec.get('low_confidence', '?')}")


def process(stream, msg_id, rec):
    print(format_record(stream, msg_id, rec), flush=True)


class Worker:
    def __init__(self, r, stream, name):
        self.r, self.stream, self.name = r, stream, name
        self.group = config.group_for(stream)
        self.done = 0
        self.last_reclaim = 0.0

    def handle(self, msg_id, rec):
        try:
            process(self.stream, msg_id, rec)
        except Exception as e:  # a bad record must not kill the worker; leave it pending and move on
            print(f"[{self.name}] {msg_id} failed: {e!r} (left pending)", flush=True)
            return
        self.r.xack(self.stream, self.group, msg_id)
        self.done += 1

    def step(self):
        if time.time() - self.last_reclaim >= config.RECLAIM_EVERY_S:
            n = runtime.reclaim(self.r, self.stream, self.group, self.name, self.handle, WORKER_RECLAIM_IDLE_MS)
            if n:
                print(f"[{self.name}] reclaimed {n} message(s)", flush=True)
            self.last_reclaim = time.time()
        resp = self.r.xreadgroup(self.group, self.name, {self.stream: ">"}, count=50, block=config.BLOCK_MS)
        for _stream, msgs in resp or []:
            for msg_id, rec in msgs:
                if runtime.stop.is_set():
                    return
                self.handle(msg_id, rec)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stream", choices=config.DECISION_STREAMS, metavar="STREAM",
                    help="one of: " + ", ".join(config.DECISION_STREAMS))
    ap.add_argument("name", nargs="?", default="worker-1")
    args = ap.parse_args()

    r = config.connect()
    runtime.install_signal_handlers(args.name)
    config.ensure_groups(r)
    w = Worker(r, args.stream, args.name)
    print(f"[{args.name}] consuming {args.stream} as {w.group}", flush=True)
    try:
        runtime.run_loop(r, w.step, args.name)
    except KeyboardInterrupt:
        pass
    print(f"[{args.name}] shut down after {w.done} messages", flush=True)


if __name__ == "__main__":
    main()
