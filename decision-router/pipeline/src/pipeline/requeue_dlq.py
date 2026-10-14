"""Move dead-lettered events back onto the ingestion stream (e.g. after an outage or a config fix).
Each is re-added with its original fields (the dead-letter fields are stripped) and removed from the DLQ.
usage: python run.py requeue [--reason SUBSTRING] [--limit N] [--dry-run]"""
from __future__ import annotations

import argparse

from pipeline import config

DLQ_FIELDS = {"source_id", "reason", "attempts"}


def main(argv: list[str] | None = None) -> None:
    cfg = config.load()
    ap = argparse.ArgumentParser()
    ap.add_argument("--reason", default="", help="only events whose reason contains this text")
    ap.add_argument("--limit", type=int, default=0, help="at most this many (0 = all)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    r = config.connect(cfg)
    moved = 0
    for dlq_id, rec in r.xrange(cfg.dlq):
        if args.reason and args.reason not in rec.get("reason", ""):
            continue
        if args.limit and moved >= args.limit:
            break
        if not args.dry_run:
            pipe = r.pipeline(transaction=True)  # re-add and remove together
            pipe.xadd(cfg.ingestion, {k: v for k, v in rec.items() if k not in DLQ_FIELDS},
                      maxlen=cfg.maxlen, approximate=True)
            pipe.xdel(cfg.dlq, dlq_id)
            pipe.execute()
        moved += 1
    print(f"{'would requeue' if args.dry_run else 'requeued'} {moved} event(s); {r.xlen(cfg.dlq)} left in {cfg.dlq}")


if __name__ == "__main__":
    main()
