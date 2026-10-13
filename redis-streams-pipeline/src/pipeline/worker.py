"""Generic consumer for one decision stream: log and ack. Replace `process` with real downstream work.
Messages abandoned by a dead worker are reclaimed after WORKER_RECLAIM_IDLE_MS.
usage: python run.py worker <stream> [NAME]"""
from __future__ import annotations

import argparse
import time

import redis

from pipeline import config, runtime

WORKER_RECLAIM_IDLE_MS = 60_000


def format_record(stream: str, msg_id: str, rec: dict) -> str:
    try:
        conf = f"{float(rec.get('confidence')):.3f}"
    except (TypeError, ValueError):
        conf = rec.get("confidence", "?")
    return (f"{stream} {msg_id} id={rec.get('id')} label={rec.get('label')} conf={conf} "
            f"source={rec.get('source', '?')} low_confidence={rec.get('low_confidence', '?')}")


def process(stream: str, msg_id: str, rec: dict) -> None:
    print(format_record(stream, msg_id, rec), flush=True)


class Worker:
    def __init__(self, r: redis.Redis, cfg: config.Config, stream: str, name: str):
        self.r, self.cfg, self.stream, self.name = r, cfg, stream, name
        self.group = cfg.group_for(stream)
        self.done = 0
        self.last_reclaim = 0.0

    def handle(self, msg_id: str, rec: dict) -> None:
        try:
            process(self.stream, msg_id, rec)
        except Exception as e:  # a bad record must not kill the worker; leave it pending and move on
            print(f"[{self.name}] {msg_id} failed: {e!r} (left pending)", flush=True)
            return
        self.r.xack(self.stream, self.group, msg_id)
        self.done += 1

    def step(self) -> None:
        if time.time() - self.last_reclaim >= self.cfg.reclaim_every_s:
            n = runtime.reclaim(self.r, self.stream, self.group, self.name, self.handle, WORKER_RECLAIM_IDLE_MS)
            if n:
                print(f"[{self.name}] reclaimed {n} message(s)", flush=True)
            self.last_reclaim = time.time()
        resp = self.r.xreadgroup(self.group, self.name, {self.stream: ">"}, count=50, block=self.cfg.block_ms)
        for _stream, msgs in resp or []:
            for msg_id, rec in msgs:
                if runtime.stop.is_set():
                    return
                self.handle(msg_id, rec)


def main(argv: list[str] | None = None) -> None:
    cfg = config.load()
    ap = argparse.ArgumentParser()
    ap.add_argument("stream", choices=cfg.decision_streams, metavar="STREAM",
                    help="one of: " + ", ".join(cfg.decision_streams))
    ap.add_argument("name", nargs="?", default="worker-1")
    args = ap.parse_args(argv)

    r = config.connect(cfg)
    runtime.install_signal_handlers(args.name)
    config.ensure_groups(r, cfg)
    w = Worker(r, cfg, args.stream, args.name)
    print(f"[{args.name}] consuming {args.stream} as {w.group}", flush=True)
    try:
        runtime.run_loop(r, cfg, w.step, args.name)
    except KeyboardInterrupt:
        pass
    print(f"[{args.name}] shut down after {w.done} messages", flush=True)


if __name__ == "__main__":
    main()
