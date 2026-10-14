"""Publish test JSON events to the ingestion stream.   usage: python run.py producer --count 1000 --rate 200
(Real data sources: XADD flat string fields to the ingestion stream, including the configured context_field.)"""
from __future__ import annotations

import argparse
import json
import time
import uuid
from typing import Callable

import redis

from pipeline import config


def non_negative(kind: Callable) -> Callable:
    def parse(s: str):
        v = kind(s)
        if v < 0:
            raise argparse.ArgumentTypeError("must be >= 0")
        return v
    return parse


def main(argv: list[str] | None = None) -> None:
    cfg = config.load()
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=non_negative(int), default=1000)
    ap.add_argument("--rate", type=non_negative(float), default=0, help="events per second (0 = as fast as possible)")
    args = ap.parse_args(argv)

    r = config.connect(cfg)
    config.ensure_groups(r, cfg)
    start, sent = time.time(), 0
    try:
        while sent < args.count:
            if args.rate > 0:
                delay = start + sent / args.rate - time.time()
                if delay > 0:
                    time.sleep(delay)
            event = {"id": str(uuid.uuid4()), cfg.context_field: json.dumps({"n": sent, "text": f"event {sent}"}),
                     "ts": time.time()}
            try:
                r.xadd(cfg.ingestion, event, maxlen=cfg.maxlen, approximate=True)
            except (redis.ConnectionError, redis.TimeoutError) as e:
                print(f"redis unavailable ({e}); retrying in 2s", flush=True)
                time.sleep(2)
                continue  # same event is retried, so nothing is skipped
            sent += 1
            if sent % 100 == 0:
                print(f"produced {sent}/{args.count}", flush=True)
    except KeyboardInterrupt:
        print(f"interrupted after {sent} events")
        return
    print(f"done: {sent} events in {time.time() - start:.1f}s")


if __name__ == "__main__":
    main()
